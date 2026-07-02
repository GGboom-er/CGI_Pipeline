# mcp_server/internals.py
# ── 内部工具函数 ──
# 从 server.py 拆分：Celery 提交、审计读取、Worker 管理等内部逻辑

import os
import sys
import json
import uuid
import time
import subprocess
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(os.getenv('PROJECT_ROOT', '.'))
AUDIT_DIR = PROJECT_ROOT / 'audit'

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.skill_registry import get_all_skills, get_skill_map, get_skill_dcc
from core.manifest import get_dcc_queue, get_dcc_queue_map
from core.task_status import TERMINAL_STATUSES

_SKILLS = get_all_skills()
_SKILL_MAP = get_skill_map()
_DCC_QUEUE_MAP = get_dcc_queue_map()


def _is_pid_alive(pid: int) -> bool:
    """进程存活检测"""
    if _HAS_PSUTIL:
        return psutil.pid_exists(pid)
    else:
        try:
            import os
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _resolve_queue(skill_id: str) -> str:
    """根据技能注册的 DCC 类型返回对应队列名"""
    dcc = get_skill_dcc(skill_id)
    return get_dcc_queue(dcc)


def _ensure_worker(dcc: str) -> tuple[bool, str]:
    """检测 Worker 是否健康；PID 活着但 Celery 心跳丢失时自动重启。"""
    from core.service_manager import ensure_worker_healthy
    return ensure_worker_healthy(dcc)


def _submit_to_celery(skill_id: str, payload: dict) -> dict:
    """统一 Celery / Foreground 提交入口"""
    task_id = payload.get('task_id', f"task-{uuid.uuid4().hex[:12]}")
    payload['task_id'] = task_id
    payload['skill_id'] = skill_id
    payload['submitted_at'] = time.time()
    
    execution_mode = payload.get('parameters', {}).get('execution_mode', 'background')
    if execution_mode == 'foreground':
        from mcp_server.foreground_client import submit_foreground_task
        # foreground 默认走 sync：客户端 schema 缓存里可能没 sync 字段（MCP 协议
        # tools/list_changed 通知不一定被客户端响应），所以由服务端强制默认开，
        # 体验跟 Script Editor 一致。显式 sync=False 可回退原异步行为。
        params_in = payload.get('parameters', {})
        sync_flag = params_in.get('sync', True)
        if sync_flag:
            return submit_foreground_task(payload, sync=True)
        # 异步分支：fire-and-forget，用后台线程写 audit
        import threading
        threading.Thread(target=submit_foreground_task, args=(payload,), daemon=True).start()
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'skill_id': skill_id,
            'message': '任务已派发至前台 Maya 实例执行 (异步跟踪中)',
        }
        
    # ── 后台模式：清除框架路由参数，防止 Worker 端 Schema 校验拒绝 ──
    params = payload.get('parameters', {})
    params.pop('execution_mode', None)
    params.pop('foreground_port', None)
    params.pop('sync', None)
    
    queue = _resolve_queue(skill_id)

    # 确保 Redis 就绪后再拉起 Worker
    from core.service_manager import start_redis
    if not start_redis():
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'error': 'Redis 未运行且启动失败',
            'recovery_hint': '请检查 Memurai/Redis 是否已安装并可执行',
        }
    ok, worker_msg = _ensure_worker(_SKILL_MAP.get(skill_id, {}).get('dcc', 'maya'))
    if not ok:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'error': worker_msg,
            'recovery_hint': '请检查 Redis/Celery Worker 日志，或调用 pipeline_service_status 查看心跳状态。',
        }

    try:
        from core.service_manager import get_celery_app
        _app = get_celery_app()
        result = _app.send_task('core.tasks.execute_dcc_skill', args=[payload], task_id=task_id, queue=queue)
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'skill_id': skill_id,
            'message': f'任务已提交至 {queue} 队列',
        }
    except Exception as e:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'error': str(e),
            'recovery_hint': '请检查：1) Redis 是否运行 2) Celery Worker 是否启动 3) Maya 是否可用',
        }


def _submit_chain(payload: dict) -> dict:
    """提交链式执行任务"""
    task_id = f"chain-{uuid.uuid4().hex[:8]}"
    payload['task_id'] = task_id
    payload['submitted_at'] = time.time()
    
    execution_mode = payload.get('execution_mode', 'background')
    if execution_mode == 'foreground':
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'error': '前台模式目前暂不支持链式执行 (Execute Chain)，请对单个技能使用前台模式。',
        }
        
    skill_chain = payload.get('skill_chain', [])
    first_skill = skill_chain[0]['skill_id'] if skill_chain else 'ping'
    queue = _resolve_queue(first_skill)
    ok, worker_msg = _ensure_worker(_SKILL_MAP.get(first_skill, {}).get('dcc', 'maya'))
    if not ok:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'dispatched': False,
            'error': worker_msg,
            'recovery_hint': '请检查 Redis/Celery Worker 日志，或调用 pipeline_service_status 查看心跳状态。',
        }

    try:
        from core.service_manager import get_celery_app
        _app = get_celery_app()
        _app.send_task('core.tasks.execute_skill_chain', args=[payload], task_id=task_id, queue=queue)
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'dispatched': True,
            'steps': len(skill_chain),
            'message': f'链式任务已提交至 {queue}，共 {len(skill_chain)} 步',
        }
    except Exception as e:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'dispatched': False,
            'error': str(e),
            'recovery_hint': '请检查 Redis 和 Celery Worker 状态',
        }


def _submit_workflow(payload: dict) -> dict:
    """提交工作流执行任务（独立 workflow_queue，不与 DCC 竞争）"""
    task_id = f"wf-{uuid.uuid4().hex[:8]}"
    payload['task_id'] = task_id
    payload['submitted_at'] = time.time()

    # 工作流编排器跑在独立的 workflow_queue 上，避免与 DCC Worker 死锁
    queue = 'workflow_queue'

    # 确保有 Worker 消费 workflow_queue
    ok, worker_msg = _ensure_worker('workflow')
    if not ok:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'workflow_id': payload.get('workflow_id', ''),
            'error': worker_msg,
            'recovery_hint': '请检查 workflow worker 日志，或调用 pipeline_service_status 查看心跳状态。',
        }

    # 同时确保工作流中用到的 DCC Worker 也在运行
    from core.workflow_engine import load_workflow
    try:
        wf = load_workflow(payload['workflow_id'])
        seen_dcc = set()
        for step in wf.get('steps', []):
            dcc = get_skill_dcc(step['skill_id'])
            if dcc not in seen_dcc:
                seen_dcc.add(dcc)
                ok, worker_msg = _ensure_worker(dcc)
                if not ok:
                    return {
                        'task_id': task_id,
                        'status': 'SUBMIT_FAILED',
                        'workflow_id': payload.get('workflow_id', ''),
                        'error': worker_msg,
                    }
    except Exception:
        ok, worker_msg = _ensure_worker('maya')
        if not ok:
            return {
                'task_id': task_id,
                'status': 'SUBMIT_FAILED',
                'workflow_id': payload.get('workflow_id', ''),
                'error': worker_msg,
            }

    try:
        from core.service_manager import get_celery_app
        _app = get_celery_app()
        _app.send_task('core.tasks.execute_workflow', args=[payload], task_id=task_id, queue=queue)
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'workflow_id': payload['workflow_id'],
            'message': f'工作流已提交至 {queue}',
        }
    except Exception as e:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'error': str(e),
        }


def _read_audit(task_id: str) -> dict:
    """读取任务状态：Redis 进度 hash → Celery PROGRESS → 审计文件。"""
    # 1. 优先从 Redis 进度 hash 读取实时状态（新架构）
    try:
        from core.progress import get_latest_state
        state = get_latest_state(task_id)
        if state and state.get('event_type'):
            evt = state.get('event_type', '')
            if evt not in ('workflow.done', 'workflow.error'):
                return {
                    'task_id': task_id,
                    'status': 'PROGRESS',
                    'detail': state.get('message', ''),
                    'step': state.get('step', -1),
                    'skill_id': state.get('skill_id', ''),
                    'timestamp': state.get('timestamp', 0),
                    'progress': state.get('progress', 0),
                    'event_type': evt,
                }
    except Exception:
        pass

    # 2. Celery 原生 PROGRESS 回退
    try:
        from core.service_manager import get_celery_app
        _app = get_celery_app()
        res = _app.AsyncResult(task_id)
        if res.state == 'PROGRESS' and isinstance(res.info, dict):
            return {
                'task_id': task_id,
                'status': 'PROGRESS',
                'meta': res.info,
            }
    except Exception:
        pass

    audit_path = None

    # 优先搜索按日期分目录的审计文件（从最新日期开始）
    if AUDIT_DIR.exists():
        date_dirs = sorted(
            [d for d in AUDIT_DIR.iterdir() if d.is_dir() and len(d.name) == 10],
            reverse=True,
        )
        for date_dir in date_dirs:
            candidate = date_dir / f'{task_id}.json'
            if candidate.exists():
                audit_path = candidate
                break

    # 扁平目录回退（兼容旧数据）
    if audit_path is None:
        flat = AUDIT_DIR / f'{task_id}.json'
        if flat.exists():
            audit_path = flat

    if audit_path is None:
        return {
            'task_id': task_id,
            'status': 'NOT_FOUND',
            'message': '未找到审计记录，任务可能尚未开始',
            'recovery_hint': '请等待 5 秒后重试。如果持续 NOT_FOUND，检查 Celery Worker 是否在运行。',
        }
    lines = audit_path.read_text(encoding='utf-8').strip().split('\n')
    entries = []
    for line in lines:
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not entries:
        return {'task_id': task_id, 'status': 'UNKNOWN', 'message': '审计文件为空'}
    latest = entries[-1]
    result = {
        'task_id': task_id,
        'status': latest.get('status', 'UNKNOWN'),
        'detail': latest.get('detail', ''),
        'step': latest.get('step', -1),
        'skill_id': latest.get('skill_id', ''),
        'timestamp': latest.get('ts', 0),
        'total_entries': len(entries),
        'entries': entries,  # 暴露全部审计条目，供调用方遍历链步骤
    }

    # 终态瘦身：全量审计可达数十万字符，会撑爆 MCP 调用方；只留末 3 条并截断长 detail。
    # 完整日志仍在审计文件（audit_path 随结果返回），需要全量直接读文件。
    if latest.get('status') in TERMINAL_STATUSES:
        def _clip(text, cap=800):
            text = text if isinstance(text, str) else str(text)
            return text if len(text) <= cap else text[:cap] + f'…[截断,共{len(text)}字符,全量见 audit_path]'
        result['detail'] = _clip(result['detail'])
        result['entries'] = [{**e, 'detail': _clip(e.get('detail', ''))} for e in entries[-3:]]
        result['audit_path'] = str(audit_path)

    # 任务到达终态时，尽量回填沙盒报告路径。成功和失败都应有报告。
    if latest.get('status') in TERMINAL_STATUSES:
        for entry in reversed(entries):
            detail = entry.get('detail', '')
            if 'report_path' not in str(detail):
                continue
            try:
                raw = detail.split(' [mem=')[0] if isinstance(detail, str) and ' [mem=' in detail else detail
                inner = json.loads(raw) if isinstance(raw, str) else raw
                outputs = inner.get('outputs', {}) if isinstance(inner, dict) else {}
                candidates = [
                    inner.get('report_path', '') if isinstance(inner, dict) else '',
                    outputs.get('report_path', ''),
                ]
                for rp in candidates:
                    if rp and Path(rp).exists():
                        result['report_path'] = rp
                        break
                if 'report_path' in result:
                    break
            except Exception:
                pass

        if 'report_path' not in result:
            report_dir = AUDIT_DIR.parent / 'reports'
            for suffix in ('', '_chain'):
                rp = report_dir / f'{task_id}{suffix}.md'
                if rp.exists():
                    result['report_path'] = str(rp)
                    break

    return result


def reload_internals():
    """热重载内部状态（由 reload_server tool 调用）"""
    global _SKILLS, _SKILL_MAP, _DCC_QUEUE_MAP
    from core.skill_registry import reload as _reload_registry
    from core.manifest import reload as _reload_manifest
    _reload_registry()
    _reload_manifest()
    _SKILLS = get_all_skills()
    _SKILL_MAP = get_skill_map()
    _DCC_QUEUE_MAP = get_dcc_queue_map()
