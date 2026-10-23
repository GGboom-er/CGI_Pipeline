# cgi_pipeline/core/dcc_factory.py
# DCC Worker 工厂 + 源文件打开逻辑（独立于 Celery，可在任意进程中使用）

import logging
import sys
import uuid

from cgi_pipeline.core.bootstrap import cfg as _cfg

logger = logging.getLogger('cgi_pipeline.dcc_factory')

_PROJECT_ROOT = _cfg.PROJECT_ROOT
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cgi_pipeline.core.capability_registry import resolve_api_id
from cgi_pipeline.core.scene_open import build_open_code, confirm_open_result


_warm_pool = {}

# 达到该任务数后回收常驻 DCC 进程，防止内存泄漏。
_WARM_POOL_RECYCLE_AFTER = 50

# 重载只做 sys.modules 剔除，是内存操作；给足余量但不能无限等，
# 否则宿主异常时整链卡死（IPC_TIMEOUT 默认 0 = 永不超时）。
_RELOAD_TIMEOUT_SEC = 60


def _sync_worker_code(pool_entry, dcc_type):
    """复用常驻进程前，若第一方源码已变则先让它重载代码。

    capability 代码在 DCC 进程内执行，进程只在首次 import 时读文件，改完源码
    直接复用就仍跑旧版（lesson L118）。这里按源码指纹自动判断：生产源码不变
    则指纹不变、永不触发；开发期改完直接跑，不需要任何手动步骤或开关。
    """
    from cgi_pipeline.core.code_reload import RELOAD_COMMAND, source_fingerprint

    current = source_fingerprint()
    if pool_entry.get('code_fingerprint') == current:
        return
    if pool_entry.get('code_fingerprint') is not None:
        logger.info(f"[{dcc_type}] 检测到源码变更，重载常驻进程内的代码。")
        try:
            # task_id 会当文件名用，只能是文件名安全字符
            result = pool_entry['worker'].run_api({
                'api_id': RELOAD_COMMAND,
                'task_id': f'reload-{uuid.uuid4().hex[:8]}',
                'timeout': _RELOAD_TIMEOUT_SEC,
            })
            if result.get('status') != 'SUCCESS':
                raise RuntimeError(f'Code reload failed: {result.get("detail", result)}')
        except Exception as exc:
            # 重载失败不静默降级成跑旧代码：关掉进程，让下次拿全新的
            logger.warning(f"[{dcc_type}] 代码重载失败，回收进程重启: {exc}")
            try:
                pool_entry['worker'].shutdown()
            except Exception:
                pass
            _warm_pool.pop(dcc_type, None)
            raise
    pool_entry['code_fingerprint'] = current


class WarmWorkerProxy:
    def __init__(self, dcc_type, source_path=None, workspace_id=None):
        self.dcc_type = dcc_type
        pool_entry = _warm_pool.get(dcc_type)
        if (pool_entry and workspace_id and pool_entry['task_count'] > _WARM_POOL_RECYCLE_AFTER
                and getattr(pool_entry['worker'], '_scene_workspace_id', None) != workspace_id):
            pool_entry['worker'].shutdown()
            _warm_pool.pop(dcc_type, None)
            pool_entry = None
        
        # 如果池中没有，或者原进程已死，则创建真实 Worker
        if not pool_entry or (pool_entry['worker'].process and pool_entry['worker'].process.poll() is not None):
            if pool_entry:
                logger.warning(f"[{dcc_type}] Warm worker 崩溃或丢失，重新启动新进程。")
                try:
                    pool_entry['worker']._cleanup_ipc_dirs()
                except Exception:
                    pass
            
            logger.info(f"[{dcc_type}] 启动新的 IPC 批处理进程 (常驻温水池模式)")
            if dcc_type == 'maya':
                from cgi_pipeline.hosts.maya.worker import MayaWorker
                real_w = MayaWorker()
            elif dcc_type == 'blender':
                from cgi_pipeline.hosts.blender.worker import BlenderWorker
                # 对于常驻池，Blender 不再初始带入 source_path，而是依靠 open_source_file 动态加载
                real_w = BlenderWorker(None)
            else:
                raise ValueError(f"不支持的常驻池类型: {dcc_type}")
                
            real_w.start()
            # 新进程刚 import 当前源码，指纹直接记为现值，不触发多余重载
            from cgi_pipeline.core.code_reload import source_fingerprint
            _warm_pool[dcc_type] = {
                'worker': real_w,
                'task_count': 0,
                'code_fingerprint': source_fingerprint(),
            }
            pool_entry = _warm_pool[dcc_type]
        else:
            # 复用既有进程：源码变了先重载，避免跑它启动时导入的旧版
            _sync_worker_code(pool_entry, dcc_type)

        self.real_worker = pool_entry['worker']
        pool_entry['task_count'] += 1
        self.worker_id = self.real_worker.worker_id

    def start(self):
        """兼容旧调用方。

        WarmWorkerProxy 在 __init__ 中已经确保真实 DCC worker 启动；
        CLI 和旧测试仍可能显式调用 start()，这里保持幂等 no-op。
        """
        return True
        
    def run_api(self, payload):
        return self.real_worker.run_api(payload)

    def run_batch(self, payloads, atomic=True):
        return self.real_worker.run_batch(payloads, atomic=atomic)
        
    def get_memory_gb(self):
        if hasattr(self.real_worker, 'get_memory_gb'):
            return self.real_worker.get_memory_gb()
        return -1.0
        
    def shutdown(self):
        # 拦截关闭请求，保留进程，除非达到回收阈值防止内存泄漏
        pool_entry = _warm_pool.get(self.dcc_type)
        if pool_entry and getattr(pool_entry['worker'], '_scene_workspace_id', None):
            # Recycle at the next workspace switch, never discard a live continuation.
            return
        if pool_entry and pool_entry['task_count'] > _WARM_POOL_RECYCLE_AFTER:
            logger.info(
                f"[{self.dcc_type}] 常驻进程已执行 {_WARM_POOL_RECYCLE_AFTER} 次任务，"
                f"触发定期回收释放内存。"
            )
            try:
                self.real_worker.shutdown()
            except Exception:
                pass
            _warm_pool.pop(self.dcc_type, None)


def create_worker(dcc_type: str = 'maya', source_path: str = None, workspace_id: str = None):
    # Maya 和 Blender 启用温水池代理
    if dcc_type in ('maya', 'blender'):
        return WarmWorkerProxy(dcc_type, source_path, workspace_id)
        
    if dcc_type in ('ue', 'pipeline'):
        from cgi_pipeline.core.pipeline_worker import PipelineWorker
        w = PipelineWorker()
    else:
        raise ValueError(f'不支持的 DCC 类型: {dcc_type}')
    w.start()
    logger.info(f'{dcc_type} Worker 已启动, ID={w.worker_id}')
    return w


def open_source_file(worker, task_id: str, source_path: str, dcc_type: str):
    """在 DCC Worker 中打开源文件。返回 (ok, error_msg)。
        对 .abc 等非原生格式，新建空场景（由后续 API 负责导入）。
    """
    getattr(worker, 'real_worker', worker)._scene_workspace_id = None
    if dcc_type in ('pipeline', 'ue'):
        return True, ''
    open_api = resolve_api_id('blender_exec_code' if dcc_type == 'blender' else 'exec_code')
    open_payload = {
        'task_id': f'{task_id}_open',
        'api_id': open_api,
        'source_path': '',
        'api_params': {
            'code': build_open_code(source_path, dcc_type),
            'description': '自动打开源文件',
        },
        'api_context': {
            'execution_mode': 'background',
            'project': '',
            'asset_name': '',
            'source_path': '',
        },
    }
    open_result = worker.run_api(open_payload)
    return confirm_open_result(open_result, source_path, dcc_type)
