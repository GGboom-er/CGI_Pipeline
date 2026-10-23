# cgi_pipeline/hosts/maya/adapter.py
# 用法：mayapy.exe -script src\cgi_pipeline\hosts\maya\adapter.py
# ── Maya 批处理适配器 ──

import json, os, time, sys, traceback
from pathlib import Path

WORKER_ID = os.environ.get('CGI_WORKER_ID', 'default')
PROJECT_ROOT = Path(os.environ.get('CGI_PROJECT_ROOT', '.'))
SOURCE_ROOT = Path(__file__).resolve().parents[3]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from cgi_pipeline.hosts.file_ipc import FileIpcTransport
from cgi_pipeline.hosts.runtime import host_runtime

RUNTIME = host_runtime(PROJECT_ROOT)

import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
if RUNTIME.threads is not None:
    # Maya 2025 threadCount is called once on this adapter's startup main thread.
    cmds.threadCount(numberOfThreads=RUNTIME.threads)
# ── 批处理模式防弹窗设置 ──
cmds.optionVar(iv=('fileIgnoreVersion', 1))         # 忽略文件版本不匹配
cmds.optionVar(iv=('useSaveScenePanelLayout', 0))   # 不弹存储面板布局

IPC = FileIpcTransport(WORKER_ID, RUNTIME.root)
CMD_FILE = IPC.cmd_file
RESULT_DIR = IPC.result_dir
POLL_INTERVAL = float(os.environ.get('IPC_POLL_INTERVAL_SEC', '0.2'))

def _write_result(task_id: str, status: str, detail: str = ''):
    IPC.publish_result(task_id, status, detail)

def _dispatch_api(payload: dict):
    '''在 Maya 主线程内执行 API，由 evalDeferred 调用'''
    task_id = payload.get('task_id', 'unknown')
    api_id = payload.get('api_id', '')
    try:
        if api_id == '__BATCH__':
            from cgi_pipeline.execution import execute_batch
            api_context = dict(payload.get('batch', [{}])[0].get('api_context') or {}) if payload.get('batch') else {}
            api_context.setdefault('execution_mode', 'background')
            api_context['cmds_module'] = cmds
            batch_result = execute_batch(payload.get('batch') or [], context=api_context,
                                         atomic=bool(payload.get('atomic', True)))
            _write_result(task_id, 'SUCCESS' if batch_result.get('status') == 'SUCCESS' else 'ERROR',
                          json.dumps(batch_result, ensure_ascii=False, default=str))
            return
        if not api_id:
            _write_result(task_id, 'ERROR', '缺少 api_id；Maya 适配器只接受 API 调用。')
            return
        # API 是确定性原子能力；适配器只提供 Maya 上下文，执行仍由
        # cgi_pipeline.execution 统一做 manifest/receipt 校验。
        from cgi_pipeline.execution import execute_api
        api_context = dict(payload.get('api_context') or {})
        api_context.setdefault('execution_mode', 'background')
        api_context.setdefault('source_path', payload.get('source_path', ''))
        api_context.setdefault('project', payload.get('project', ''))
        api_context.setdefault('asset_name', payload.get('asset_name', ''))
        api_context['cmds_module'] = cmds
        api_result = execute_api(
            api_id,
            payload.get('api_params') or payload.get('params') or {},
            api_context,
        )

        # 🛑 第一性原则：API执行后安全检查
        api_status = api_result.get('status', '') if isinstance(api_result, dict) else 'ERROR'

        try:
            from cgi_pipeline.core.path_guard import is_protected_path
            # 使用原生调用，绕过补丁
            current_scene = _original_file_cmd(query=True, sceneName=True) or ''
            if current_scene and is_protected_path(current_scene):
                _original_file_cmd(rename='')
                if isinstance(api_result, dict):
                    api_result['_guard_warning'] = (
                        f'安全守卫：已断开受保护路径 "{current_scene}" 的关联，场景数据保留。'
                    )
        except Exception:
            pass

        from cgi_pipeline.contracts import API_STATUSES
        result_status = api_status if api_status in API_STATUSES else 'ERROR'
        _write_result(task_id, result_status, json.dumps(api_result, ensure_ascii=False, default=str))
    except Exception as e:
        _write_result(task_id, 'ERROR', traceback.format_exc())

def _poll_loop():
    '''主线程轮询，修复了读写竞态条件'''
    print(f'[Adapter] Worker {WORKER_ID} ready. Polling {CMD_FILE}')
    while True:
        try:
            payload = IPC.claim()
            if payload is not None:
                if payload.get('api_id') == '__DIE__':
                    import os as _os
                    _os._exit(0)
                if payload.get('api_id') == '__RELOAD__':
                    # 剔除第一方模块，下次 execute_api 重新 import 到新代码；
                    # maya.cmds 与当前场景是 Maya 自身状态，不受影响。
                    from cgi_pipeline.core.code_reload import evict_capability_modules
                    evicted = evict_capability_modules()
                    _write_result(payload.get('task_id', 'reload'), 'SUCCESS',
                                  f'evicted {evicted} modules')
                    continue
                _dispatch_api(payload)
        except Exception as e:
            sys.stderr.write(f'[Adapter Poll Error] {traceback.format_exc()}\n')
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    # ── 安全补丁：拦截恶意/误操作覆写生产服务器文件 ──
    _original_file_cmd = cmds.file
    def _safe_file_cmd(*args, **kwargs):
        from cgi_pipeline.core.path_guard import is_protected_path
        is_writing = any(k in kwargs for k in ['save', 's', 'rename', 'exportAll', 'ea', 'exportSelected', 'es'])
        if is_writing:
            paths = [arg for arg in args if isinstance(arg, str)]
            if 'rename' in kwargs and isinstance(kwargs['rename'], str):
                paths.append(kwargs['rename'])
            if ('save' in kwargs or 's' in kwargs) and not paths:
                paths.append(_original_file_cmd(query=True, sceneName=True) or '')
                
            for p in paths:
                if p and is_protected_path(p):
                    raise PermissionError(f"[安全拦截] exec_code 安全边界触发：严禁覆盖受保护的生产路径: {p}")
                    
        return _original_file_cmd(*args, **kwargs)
    cmds.file = _safe_file_cmd

    _poll_loop()
