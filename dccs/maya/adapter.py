# dccs/maya/adapter.py
# 用法：mayapy.exe -script dccs\maya\adapter.py
# ── CGI Pipeline v2.0 — 修正版（含缺陷 4 补丁）──

import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
# ── 批处理模式防弹窗设置 ──
cmds.optionVar(iv=('fileIgnoreVersion', 1))         # 忽略文件版本不匹配
cmds.optionVar(iv=('useSaveScenePanelLayout', 0))   # 不弹存储面板布局
import json, os, time, sys, traceback
from pathlib import Path

WORKER_ID = os.environ.get('CGI_WORKER_ID', 'default')
PROJECT_ROOT = Path(os.environ.get('CGI_PROJECT_ROOT', '.'))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CMD_FILE  = PROJECT_ROOT / 'ipc' / 'cmd'    / WORKER_ID / 'pending.json'
RESULT_DIR = PROJECT_ROOT / 'ipc' / 'result' / WORKER_ID
POLL_INTERVAL = float(os.environ.get('IPC_POLL_INTERVAL_SEC', '0.2'))

CMD_FILE.parent.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)

def _write_result(task_id: str, status: str, detail: str = ''):
    result = {'task_id': task_id, 'status': status, 'detail': detail}
    tmp = RESULT_DIR / f'{task_id}.tmp'
    final = RESULT_DIR / f'{task_id}.json'
    tmp.write_text(json.dumps(result))
    os.replace(tmp, final)  # 原子操作，防止宿主进程读到半写文件

def _dispatch_skill(payload: dict):
    '''在 Maya 主线程内执行技能，由 evalDeferred 调用'''
    task_id = payload.get('task_id', 'unknown')
    skill_id = payload.get('skill_id', '')
    try:
        # 动态导入 skills 下的对应模块
        import importlib
        try:
            skill_module = importlib.import_module(f'skills.{skill_id}.{skill_id}')
        except ImportError:
            _write_result(task_id, 'ERROR', f'Skill "{skill_id}" not found. Must exist in skills/{skill_id}/{skill_id}.py')
            return
            
        import maya.api.OpenMaya as om
        import maya.api.OpenMayaAnim as oma
        
        # 强制重新加载以支持热更新
        importlib.reload(skill_module)
        
        # 执行技能
        skill_result = skill_module.execute(payload)

        # 🛑 第一性原则：技能执行后安全检查
        _skill_status = ''
        if isinstance(skill_result, dict):
            _skill_status = skill_result.get('status', '')

        try:
            from core.path_guard import is_protected_path
            # 使用原生调用，绕过补丁
            current_scene = _original_file_cmd(query=True, sceneName=True) or ''
            if current_scene and is_protected_path(current_scene):
                _original_file_cmd(rename='')
                if isinstance(skill_result, dict):
                    skill_result['_guard_warning'] = (
                        f'安全守卫：已断开受保护路径 "{current_scene}" 的关联，场景数据保留。'
                    )
        except Exception:
            pass

        result_status = 'SUCCESS'
        if isinstance(skill_result, dict) and skill_result.get('status') in ('ERROR', 'BLOCKED', 'AUDIT_FAILED', 'NEEDS_ATTENTION'):
            result_status = skill_result['status']
        _write_result(task_id, result_status, json.dumps(skill_result, ensure_ascii=False, default=str))
    except Exception as e:
        _write_result(task_id, 'ERROR', traceback.format_exc())

def _poll_loop():
    '''主线程轮询，修复了读写竞态条件'''
    print(f'[Adapter] Worker {WORKER_ID} ready. Polling {CMD_FILE}')
    processing_file = CMD_FILE.with_name('processing.json')
    while True:
        try:
            if CMD_FILE.exists():
                try:
                    os.replace(CMD_FILE, processing_file)
                except OSError:
                    time.sleep(0.01)
                    continue
                raw = processing_file.read_text()
                processing_file.unlink()
                payload = json.loads(raw)
                if payload.get('skill_id') == '__DIE__':
                    import os as _os
                    _os._exit(0)
                _dispatch_skill(payload)
        except Exception as e:
            sys.stderr.write(f'[Adapter Poll Error] {traceback.format_exc()}\n')
        time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    # ── 安全补丁：拦截恶意/误操作覆写生产服务器文件 ──
    _original_file_cmd = cmds.file
    def _safe_file_cmd(*args, **kwargs):
        from core.path_guard import is_protected_path
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
