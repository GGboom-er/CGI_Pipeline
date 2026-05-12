# dccs/ue/adapter.py
# ── CGI Pipeline v2.0 — UE Editor 端 IPC 适配器 ──
#
# 用法：UnrealEditor-Cmd.exe -ExecutePythonScript dccs/ue/adapter.py
#
# 在 UE Editor commandlet 模式中运行，轮询 IPC 指令。
# UE Python 仅限 Editor 工具脚本，禁止 Runtime 调用。

import json, os, time, sys, traceback, importlib
from pathlib import Path

# UE Python 环境中 unreal 模块可用
try:
    import unreal
    _HAS_UNREAL = True
except ImportError:
    _HAS_UNREAL = False

WORKER_ID    = os.environ.get('CGI_WORKER_ID', 'default')
PROJECT_ROOT = Path(os.environ.get('CGI_PROJECT_ROOT', '.'))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CMD_FILE     = PROJECT_ROOT / 'ipc' / 'cmd' / WORKER_ID / 'pending.json'
RESULT_DIR   = PROJECT_ROOT / 'ipc' / 'result' / WORKER_ID
POLL_INTERVAL = float(os.environ.get('IPC_POLL_INTERVAL_SEC', '1.0'))

CMD_FILE.parent.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)


def _write_result(task_id: str, status: str, detail: str = ''):
    result = {'task_id': task_id, 'status': status, 'detail': detail}
    tmp = RESULT_DIR / f'{task_id}.tmp'
    final = RESULT_DIR / f'{task_id}.json'
    tmp.write_text(json.dumps(result))
    os.replace(tmp, final)


def _dispatch_skill(payload: dict):
    task_id = payload.get('task_id', 'unknown')
    skill_id = payload.get('skill_id', '')
    try:
        try:
            skill_module = importlib.import_module(f'skills.{skill_id}')
        except ImportError:
            _write_result(task_id, 'ERROR',
                         f'Skill "{skill_id}" not found in skills/{skill_id}.py')
            return

        importlib.reload(skill_module)
        skill_result = skill_module.execute(payload)
        _write_result(task_id, 'SUCCESS', str(skill_result))
    except Exception:
        _write_result(task_id, 'ERROR', traceback.format_exc())


def _poll_loop():
    ue_ver = ''
    if _HAS_UNREAL:
        ue_ver = unreal.SystemLibrary.get_engine_version()
    print(f'[UE Adapter] Worker {WORKER_ID} ready. UE={ue_ver}. Polling {CMD_FILE}')
    while True:
        try:
            if CMD_FILE.exists():
                raw = CMD_FILE.read_text()
                CMD_FILE.unlink()
                payload = json.loads(raw)
                if payload.get('skill_id') == '__DIE__':
                    break
                _dispatch_skill(payload)
        except Exception:
            sys.stderr.write(f'[UE Adapter Error] {traceback.format_exc()}\n')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    _poll_loop()
