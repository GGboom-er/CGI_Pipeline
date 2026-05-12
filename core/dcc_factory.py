# core/dcc_factory.py
# DCC Worker 工厂 + 源文件打开逻辑（独立于 Celery，可在任意进程中使用）

import logging
import json
import os
import sys
from pathlib import Path

from core.bootstrap import cfg as _cfg

logger = logging.getLogger('cgi_pipeline.dcc_factory')

_PROJECT_ROOT = _cfg.PROJECT_ROOT
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.skill_registry import get_skill_dcc


_warm_pool = {}

class WarmWorkerProxy:
    def __init__(self, dcc_type, source_path=None):
        self.dcc_type = dcc_type
        pool_entry = _warm_pool.get(dcc_type)
        
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
                from dccs.maya.worker import MayaWorker
                real_w = MayaWorker()
            elif dcc_type == 'blender':
                from dccs.blender.worker import BlenderWorker
                # 对于常驻池，Blender 不再初始带入 source_path，而是依靠 open_source_file 动态加载
                real_w = BlenderWorker(None)
            else:
                raise ValueError(f"不支持的常驻池类型: {dcc_type}")
                
            real_w.start()
            _warm_pool[dcc_type] = {'worker': real_w, 'task_count': 0}
            pool_entry = _warm_pool[dcc_type]
            
        self.real_worker = pool_entry['worker']
        pool_entry['task_count'] += 1
        self.worker_id = self.real_worker.worker_id
        
    def run_skill(self, payload):
        return self.real_worker.run_skill(payload)
        
    def get_memory_gb(self):
        if hasattr(self.real_worker, 'get_memory_gb'):
            return self.real_worker.get_memory_gb()
        return -1.0
        
    def shutdown(self):
        # 拦截关闭请求，保留进程，除非执行次数达到 50 次防止内存泄漏
        pool_entry = _warm_pool.get(self.dcc_type)
        if pool_entry and pool_entry['task_count'] > 50:
            logger.info(f"[{self.dcc_type}] 常驻进程已执行 50 次任务，触发定期回收释放内存。")
            try:
                self.real_worker.shutdown()
            except Exception:
                pass
            _warm_pool.pop(self.dcc_type, None)


def create_worker(dcc_type: str = 'maya', source_path: str = None):
    # Maya 和 Blender 启用温水池代理
    if dcc_type in ('maya', 'blender'):
        return WarmWorkerProxy(dcc_type, source_path)
        
    if dcc_type == 'ue':
        from dccs.ue.worker import UEWorker
        w = UEWorker()
    elif dcc_type == 'pipeline':
        from core.pipeline_worker import PipelineWorker
        w = PipelineWorker()
    else:
        raise ValueError(f'不支持的 DCC 类型: {dcc_type}')
    w.start()
    logger.info(f'{dcc_type} Worker 已启动, ID={w.worker_id}')
    return w


def open_source_file(worker, task_id: str, source_path: str, dcc_type: str):
    """在 DCC Worker 中打开源文件。返回 (ok, error_msg)。
    对 .abc 等非原生格式，新建空场景（由后续 skill 自行导入）。
    """
    import json as _json
    safe_path = _json.dumps(source_path)
    ext = os.path.splitext(source_path)[1].lower()
    non_native = ext in ('.abc', '.fbx', '.obj', '.usd', '.usda', '.usdc')

    if dcc_type == 'pipeline':
        return True, ''

    if dcc_type == 'blender':
        open_skill = 'blender_exec_code'
        if non_native:
            open_code = (
                'import bpy\n'
                'bpy.ops.wm.read_factory_settings(use_empty=True)\n'
                'result = {"status":"SUCCESS","scene":"<new_scene>"}\n'
            )
        else:
            open_code = (
                'import bpy, os, json, time\n'
                f'sp = json.loads({safe_path!r})\n'
                'for _ in range(20):\n'
                '    if os.path.isfile(sp) and os.path.getsize(sp) > 0:\n'
                '        break\n'
                '    time.sleep(0.5)\n'
                'if os.path.isfile(sp):\n'
                '    try:\n'
                '        if bpy.data.filepath.replace("\\\\", "/") != sp.replace("\\\\", "/"):\n'
                '            bpy.ops.wm.open_mainfile(filepath=sp, load_ui=False)\n'
                '        result = {"status":"SUCCESS","scene":bpy.data.filepath}\n'
                '    except Exception as e:\n'
                '        result = {"status":"ERROR","message":f"Failed to open {sp}: {e}"}\n'
                'else:\n'
                '    result = {"status":"ERROR","message":f"文件不存在或为空 (可能由 SMB 缓存延迟导致): {sp}"}\n'
            )
    else:
        open_skill = 'exec_code'
        if non_native:
            open_code = (
                'import maya.cmds as cmds\n'
                'cmds.file(new=True, force=True)\n'
                'result = {"status":"SUCCESS","scene":"<new_scene>"}\n'
            )
        else:
            open_code = (
                'import maya.cmds as cmds; import os, json, time\n'
                f'sp = json.loads({safe_path!r})\n'
                'for _ in range(20):\n'
                '    if os.path.isfile(sp) and os.path.getsize(sp) > 0:\n'
                '        break\n'
                '    time.sleep(0.5)\n'
                'if os.path.isfile(sp):\n'
                '    cmds.file(sp, open=True, force=True)\n'
                '    result = {"status":"SUCCESS","scene":cmds.file(q=True,sn=True)}\n'
                'else:\n'
                '    result = {"status":"ERROR","message":f"文件不存在或为空 (可能由 SMB 缓存延迟导致): {sp}"}\n'
            )

    open_payload = {
        'task_id': f'{task_id}_open',
        'skill_id': open_skill,
        'source_path': '',
        'parameters': {
            'code': open_code,
            'description': '自动打开源文件',
        }
    }
    open_result = worker.run_skill(open_payload)
    if open_result.get('status') != 'SUCCESS':
        return False, open_result.get('detail', '打开文件失败')
    return True, ''
