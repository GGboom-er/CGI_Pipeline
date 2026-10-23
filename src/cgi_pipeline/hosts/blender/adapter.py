# cgi_pipeline/hosts/blender/adapter.py
# ── Blender IPC 适配器 ──
#
# 用法：blender --background --python src/cgi_pipeline/hosts/blender/adapter.py
#
# 在 Blender 后台模式中运行，轮询 IPC 指令文件，
# 通过统一 API runner 执行 API，结果写回 JSON。

import bpy
import json, os, time, sys, traceback, importlib
from pathlib import Path

WORKER_ID    = os.environ.get('CGI_WORKER_ID', 'default')
PROJECT_ROOT = Path(os.environ.get('CGI_PROJECT_ROOT', '.'))
SOURCE_ROOT = Path(__file__).resolve().parents[3]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from cgi_pipeline.hosts.file_ipc import FileIpcTransport
from cgi_pipeline.hosts.runtime import host_runtime

RUNTIME = host_runtime(PROJECT_ROOT)
IPC = FileIpcTransport(WORKER_ID, RUNTIME.root)
CMD_FILE = IPC.cmd_file
RESULT_DIR = IPC.result_dir
POLL_INTERVAL = float(os.environ.get('IPC_POLL_INTERVAL_SEC', '0.2'))

def _write_result(task_id: str, status: str, detail: str = ''):
    IPC.publish_result(task_id, status, detail)


def _dispatch_api(payload: dict):
    """在 Blender 主线程内执行一个 API。"""
    task_id = payload.get('task_id', 'unknown')
    api_id = payload.get('api_id', '')
    try:
        if api_id == '__BATCH__':
            from cgi_pipeline.execution import execute_batch
            api_context = dict(payload.get('batch', [{}])[0].get('api_context') or {}) if payload.get('batch') else {}
            api_context.setdefault('execution_mode', 'background')
            api_context['bpy_module'] = bpy
            batch_result = execute_batch(payload.get('batch') or [], context=api_context,
                                         atomic=bool(payload.get('atomic', True)))
            _write_result(task_id, 'SUCCESS' if batch_result.get('status') == 'SUCCESS' else 'ERROR',
                          json.dumps(batch_result, ensure_ascii=False, default=str))
            return
        if not api_id:
            _write_result(task_id, 'ERROR', '缺少 api_id；Blender 适配器只接受 API 调用。')
            return
        from cgi_pipeline.execution import execute_api
        api_context = dict(payload.get('api_context') or {})
        api_context.setdefault('execution_mode', 'background')
        api_context.setdefault('source_path', payload.get('source_path', ''))
        api_context.setdefault('project', payload.get('project', ''))
        api_context.setdefault('asset_name', payload.get('asset_name', ''))
        api_context['bpy_module'] = bpy
        api_result = execute_api(
            api_id,
            payload.get('api_params') or payload.get('params') or {},
            api_context,
        )

        # API 执行后安全检查：如果场景仍指向受保护路径，强制断开关联
        api_status = api_result.get('status', '') if isinstance(api_result, dict) else 'ERROR'

        try:
            from cgi_pipeline.core.path_guard import is_protected_path
            current_file = bpy.data.filepath or ''
            if current_file and is_protected_path(current_file):
                # 断开文件路径关联，防止意外保存回受保护路径
                # 但保留场景数据，允许后续 API 继续操作
                bpy.data.filepath = ''
                if isinstance(api_result, dict):
                    api_result['_guard_warning'] = (
                        f'安全守卫：已断开受保护路径 "{current_file}" 的关联，场景数据保留。'
                    )
        except Exception:
            pass

        from cgi_pipeline.contracts import API_STATUSES
        result_status = api_status if api_status in API_STATUSES else 'ERROR'
        _write_result(task_id, result_status, json.dumps(api_result, ensure_ascii=False, default=str))
    except Exception:
        _write_result(task_id, 'ERROR', traceback.format_exc())


def _poll_loop():
    """主线程轮询"""
    print(f'[Blender Adapter] Worker {WORKER_ID} ready. Polling {CMD_FILE}')
    while True:
        try:
            payload = IPC.claim()
            if payload is not None:
                if payload.get('api_id') == '__DIE__':
                    # 不在此处 import sys：函数内 import 会把 sys 变成局部名，
                    # 让下方 except 分支里的 sys.stderr 触发 UnboundLocalError
                    sys.exit(0)
                if payload.get('api_id') == '__RELOAD__':
                    # 剔除第一方模块，下次 execute_api 重新 import 到新代码；
                    # bpy 与已打开场景是 Blender 自身状态，不受影响。
                    from cgi_pipeline.core.code_reload import evict_capability_modules
                    evicted = evict_capability_modules()
                    _write_result(payload.get('task_id', 'reload'), 'SUCCESS',
                                  f'evicted {evicted} modules')
                    continue
                _dispatch_api(payload)
        except Exception:
            sys.stderr.write(f'[Adapter Poll Error] {traceback.format_exc()}\n')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    _poll_loop()
