# dccs/blender/adapter.py
# ── CGI Pipeline v2.0 — Blender 端 IPC 适配器 ──
#
# 用法：blender --background --python dccs/blender/adapter.py
#
# 在 Blender 后台模式中运行，轮询 IPC 指令文件，
# 动态加载 skills 模块执行技能，结果写回 JSON。

import bpy
import json, os, time, sys, traceback, importlib
from pathlib import Path

WORKER_ID    = os.environ.get('CGI_WORKER_ID', 'default')
PROJECT_ROOT = Path(os.environ.get('CGI_PROJECT_ROOT', '.'))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CMD_FILE     = PROJECT_ROOT / 'ipc' / 'cmd' / WORKER_ID / 'pending.json'
RESULT_DIR   = PROJECT_ROOT / 'ipc' / 'result' / WORKER_ID
POLL_INTERVAL = float(os.environ.get('IPC_POLL_INTERVAL_SEC', '0.2'))

CMD_FILE.parent.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)


def _write_result(task_id: str, status: str, detail: str = ''):
    result = {'task_id': task_id, 'status': status, 'detail': detail}
    tmp = RESULT_DIR / f'{task_id}.tmp'
    final = RESULT_DIR / f'{task_id}.json'
    tmp.write_text(json.dumps(result))
    os.replace(tmp, final)


def _dispatch_skill(payload: dict):
    """执行技能"""
    task_id = payload.get('task_id', 'unknown')
    skill_id = payload.get('skill_id', '')
    try:
        # 动态加载技能模块
        try:
            skill_module = importlib.import_module(f'skills.{skill_id}')
            if not hasattr(skill_module, 'execute'):
                skill_module = importlib.import_module(f'skills.{skill_id}.{skill_id}')
        except ImportError:
            try:
                skill_module = importlib.import_module(f'skills.{skill_id}.{skill_id}')
            except ImportError:
                _write_result(task_id, 'ERROR',
                             f'Skill "{skill_id}" not found. Tried skills.{skill_id} and skills.{skill_id}.{skill_id}')
                return

        importlib.reload(skill_module)  # 热更新支持
        skill_result = skill_module.execute(payload)

        # 技能执行后安全检查：如果场景仍指向受保护路径，强制清空
        _skill_status = ''
        if isinstance(skill_result, dict):
            _skill_status = skill_result.get('status', '')

        try:
            from core.path_guard import is_protected_path
            current_file = bpy.data.filepath or ''
            if current_file and is_protected_path(current_file):
                # 断开文件路径关联，防止意外保存回受保护路径
                # 但保留场景数据，允许后续 skill 继续操作
                bpy.data.filepath = ''
                if isinstance(skill_result, dict):
                    skill_result['_guard_warning'] = (
                        f'安全守卫：已断开受保护路径 "{current_file}" 的关联，场景数据保留。'
                    )
        except Exception:
            pass

        result_status = 'SUCCESS'
        if isinstance(skill_result, dict) and skill_result.get('status') in ('ERROR', 'BLOCKED', 'AUDIT_FAILED', 'NEEDS_ATTENTION'):
            result_status = skill_result['status']
        _write_result(task_id, result_status, json.dumps(skill_result, ensure_ascii=False, default=str))
    except Exception:
        _write_result(task_id, 'ERROR', traceback.format_exc())


def _poll_loop():
    """主线程轮询"""
    print(f'[Blender Adapter] Worker {WORKER_ID} ready. Polling {CMD_FILE}')
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
                    import sys
                    sys.exit(0)
                _dispatch_skill(payload)
        except Exception:
            sys.stderr.write(f'[Adapter Poll Error] {traceback.format_exc()}\n')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    _poll_loop()
