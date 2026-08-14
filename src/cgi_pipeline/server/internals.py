# cgi_pipeline/server/internals.py
# ── 内部工具函数 ──
# 从 server.py 拆分：Celery 提交、审计读取、Worker 管理等内部逻辑

import os
import sys
import json
import uuid
import time
import subprocess
from pathlib import Path
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
from dotenv import load_dotenv
from cgi_pipeline.paths import REPOSITORY_ROOT

load_dotenv()

PROJECT_ROOT = REPOSITORY_ROOT
AUDIT_DIR = PROJECT_ROOT / 'audit'

from cgi_pipeline.core.capability_registry import get_executor, resolve_api_id
from cgi_pipeline.core.manifest import get_executor_queue
from cgi_pipeline.core.task_status import TERMINAL_STATUSES
from cgi_pipeline.catalog import get_capability
from cgi_pipeline.contracts import ApiContractError, validate_params


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


def _ensure_worker(executor: str) -> tuple[bool, str]:
    """Keep submission fast; full Celery health checks run outside the hot path."""
    from cgi_pipeline.core.service_manager import ensure_worker_available
    return ensure_worker_available(executor)


def _append_submission_audit(task_id: str, api_id: str, queue: str, status: str = 'SUBMITTED', detail: str = '') -> Path:
    """记录提交阶段，避免排队任务或提交中断伪装成 NOT_FOUND。"""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = AUDIT_DIR / f'{task_id}.json'
    entry = {
        'task_id': task_id,
        'api_id': api_id,
        'status': status,
        'ts': time.time(),
        'detail': detail or f'任务已提交至 {queue}，等待 Worker 开始执行',
        'queue': queue,
    }
    with audit_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return audit_path


def _submit_api_to_celery(api_id: str, payload: dict) -> dict:
    """Submit one catalog API through the existing Worker/report path.

    API execution deliberately reuses ``execute_api_operation`` as the transport
    task.  The DCC adapter dispatches ``api_id`` to ``cgi_pipeline.execution``; no second
    queue, worker, or workflow implementation is created.
    """
    requested_api_id = str(api_id or '')
    try:
        api_id = resolve_api_id(requested_api_id)
    except KeyError as exc:
        task_id = payload.get('task_id', f"api-{uuid.uuid4().hex[:12]}")
        return {
            'task_id': task_id,
            'status': 'ERROR',
            'api_id': requested_api_id,
            'error_code': 'API_NOT_FOUND',
            'error': str(exc),
            'recovery_hint': 'Use list_apis or api_help to select a registered API.',
        }

    task_id = payload.get('task_id', f"api-{uuid.uuid4().hex[:12]}")
    payload['task_id'] = task_id
    payload['api_id'] = api_id
    payload['submitted_at'] = time.time()

    try:
        spec = get_capability(api_id)
    except KeyError as exc:
        return {
            'task_id': task_id,
            'status': 'ERROR',
            'api_id': api_id,
            'error_code': 'API_NOT_FOUND',
            'error': str(exc),
            'recovery_hint': 'Use list_apis or api_help to select a registered API.',
        }

    try:
        normalized_params = validate_params(spec, payload.get('params') or {})
    except ApiContractError as exc:
        return {
            'task_id': task_id,
            'status': 'ERROR',
            'api_id': api_id,
            'error_code': 'API_CONTRACT_ERROR',
            'error': str(exc),
            'recovery_hint': 'Read api_help for required inputs and choices.',
        }

    context = dict(payload.get('context') or {})
    execution_mode = str(context.get('execution_mode') or 'background')
    if execution_mode not in spec.get('execution_modes', []):
        return {
            'task_id': task_id,
            'status': 'ERROR',
            'api_id': api_id,
            'error_code': 'API_CONTRACT_ERROR',
            'error': f'{api_id} does not support execution_mode={execution_mode}',
            'recovery_hint': 'Read api_help for supported execution modes.',
        }

    payload['api_params'] = normalized_params
    payload['api_context'] = {
        **context,
        'execution_mode': execution_mode,
        'project': payload.get('project', context.get('project', '')),
        'asset_name': payload.get('asset_name', context.get('asset_name', '')),
        'source_path': payload.get('source_path', context.get('source_path', '')),
        'run_dir': context.get('run_dir', ''),
    }
    payload.pop('params', None)
    payload.pop('context', None)
    if execution_mode == 'foreground':
        executor = spec['executor']
        if executor == 'maya':
            from cgi_pipeline.server.foreground_client import submit_foreground_task
        elif executor == 'blender':
            from cgi_pipeline.hosts.blender.foreground_client import submit_foreground_task
        else:
            return {
                'task_id': task_id,
                'status': 'SUBMIT_FAILED',
                'api_id': api_id,
                'error': f'Foreground mode is not supported for executor: {executor}',
            }
        if bool(context.get('sync', True)):
            return submit_foreground_task(payload, sync=True)
        import threading
        threading.Thread(
            target=submit_foreground_task,
            args=(payload,),
            daemon=True,
        ).start()
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'api_id': api_id,
            'message': f'API 已派发至前台 {executor.title()} 实例执行',
        }

    executor = spec['executor']
    queue = get_executor_queue(executor)
    from cgi_pipeline.core.service_manager import start_redis
    if not start_redis():
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'api_id': api_id,
            'error': 'Redis 未运行且启动失败',
            'recovery_hint': '请检查 CGI 运行时服务状态。',
        }
    ok, worker_msg = _ensure_worker(executor)
    if not ok:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'api_id': api_id,
            'error': worker_msg,
            'recovery_hint': '调用 pipeline_service_status 查看 Worker 心跳。',
        }
    try:
        from cgi_pipeline.core.service_manager import get_celery_app
        app = get_celery_app()
        _append_submission_audit(
            task_id, api_id, queue, status='DISPATCHING',
            detail=f'准备提交 API 至 {queue} 队列',
        )
        app.send_task(
            'cgi_pipeline.core.tasks.execute_api_operation',
            args=[payload], task_id=task_id, queue=queue,
        )
        _append_submission_audit(task_id, api_id, queue)
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'api_id': api_id,
            'message': f'API 已提交至 {queue} 队列',
        }
    except Exception as exc:
        try:
            _append_submission_audit(
                task_id, api_id, queue, status='SUBMIT_FAILED',
                detail=f'API 提交失败: {exc}',
            )
        except Exception:
            pass
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'api_id': api_id,
            'error': str(exc),
            'recovery_hint': '请检查 Redis、Celery Worker 与 DCC 环境。',
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
            'error': '前台模式目前暂不支持链式执行 (Execute Chain)，请对单个API使用前台模式。',
        }
        
    api_chain = payload.get('api_chain', [])
    first_step = api_chain[0] if api_chain else {}
    first_api_id = resolve_api_id(str(first_step.get('api_id') or ''))
    first_step['api_id'] = first_api_id
    payload['api_chain'] = api_chain
    executor = get_capability(first_api_id)['executor']
    queue = get_executor_queue(executor)
    ok, worker_msg = _ensure_worker(executor)
    if not ok:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'dispatched': False,
            'error': worker_msg,
            'recovery_hint': '请检查 Redis/Celery Worker 日志，或调用 pipeline_service_status 查看心跳状态。',
        }

    try:
        from cgi_pipeline.core.service_manager import get_celery_app
        _app = get_celery_app()
        _app.send_task('cgi_pipeline.core.tasks.execute_api_chain', args=[payload], task_id=task_id, queue=queue)
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'dispatched': True,
            'steps': len(api_chain),
            'message': f'链式任务已提交至 {queue}，共 {len(api_chain)} 步',
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
    """提交工作流到唯一 CGI 队列；workflow 内部顺序执行各 DCC 段。"""
    task_id = f"wf-{uuid.uuid4().hex[:8]}"
    payload['task_id'] = task_id
    payload['submitted_at'] = time.time()

    queue = 'cgi_queue'

    # 先校验工作流定义，再只确保一个 CGI Worker。
    from cgi_pipeline.core.workflow_engine import load_workflow
    try:
        load_workflow(payload['workflow_id'])
    except Exception as exc:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'workflow_id': payload.get('workflow_id', ''),
            'error': f'工作流定义不可用: {exc}',
        }

    ok, worker_msg = _ensure_worker('cgi')
    if not ok:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'workflow_id': payload.get('workflow_id', ''),
            'error': worker_msg,
            'recovery_hint': '请检查 workflow worker 日志，或调用 pipeline_service_status 查看心跳状态。',
        }

    try:
        from cgi_pipeline.core.service_manager import get_celery_app
        _app = get_celery_app()
        _app.send_task('cgi_pipeline.core.tasks.execute_workflow', args=[payload], task_id=task_id, queue=queue)
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
        from cgi_pipeline.core.progress import get_latest_state
        state = get_latest_state(task_id)
        if state and state.get('event_type'):
            evt = state.get('event_type', '')
            if evt not in ('workflow.done', 'workflow.error'):
                return {
                    'task_id': task_id,
                    'status': 'PROGRESS',
                    'detail': state.get('message', ''),
                    'step': state.get('step', -1),
                    'api_id': state.get('api_id', ''),
                    'timestamp': state.get('timestamp', 0),
                    'progress': state.get('progress', 0),
                    'event_type': evt,
                }
    except Exception:
        pass

    # 2. Celery 原生 PROGRESS 回退
    try:
        from cgi_pipeline.core.service_manager import get_celery_app
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
        'api_id': latest.get('api_id', ''),
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
                outputs = inner.get('output', {}) if isinstance(inner, dict) else {}
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


def get_task_result(task_id: str) -> dict:
    """Return a task state and its normalized API receipt when available."""
    result = _read_audit(task_id)
    if result.get('status') not in TERMINAL_STATUSES:
        return result

    candidates = [result.get('detail', '')]
    candidates.extend(entry.get('detail', '') for entry in reversed(result.get('entries', [])))
    receipt_fields = {
        'api_id', 'api_version', 'status', 'input', 'output', 'elapsed_sec',
        'error_code', 'error', 'recovery_hint', 'artifacts',
    }
    for detail in candidates:
        if not isinstance(detail, str):
            continue
        try:
            receipt = json.loads(detail.split(' [mem=')[0])
        except json.JSONDecodeError:
            continue
        if isinstance(receipt, dict) and receipt_fields.issubset(receipt):
            result['receipt'] = receipt
            break
    return result


def _find_audit_file(task_id: str):
    """定位某 task_id 的审计文件（按日期分目录优先，扁平回退）。"""
    if AUDIT_DIR.exists():
        date_dirs = sorted(
            [d for d in AUDIT_DIR.iterdir() if d.is_dir() and len(d.name) == 10],
            reverse=True,
        )
        for date_dir in date_dirs:
            candidate = date_dir / f'{task_id}.json'
            if candidate.exists():
                return candidate
    flat = AUDIT_DIR / f'{task_id}.json'
    return flat if flat.exists() else None


def collect_workflow_steps(task_id: str) -> list[dict]:
    """汇总工作流每步终态。跨段子链的 workflow_id 被注入成主 wf id，所有段的
    STEP_* 都写进同一个主 wf 审计文件（非独立 seg 文件）。按文件内出现顺序把
    STEP_START → STEP_<终态> 配对，拼成有序步骤清单 [{step, api_id, status}]。
    段边界天然由顺序保持，不依赖每段自 0 起的 step 索引。
    """
    steps: list[dict] = []
    audit_path = _find_audit_file(task_id)
    if audit_path is None:
        return steps
    try:
        lines = audit_path.read_text(encoding='utf-8').strip().split('\n')
    except OSError:
        return steps
    for line in lines:
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        st = e.get('status', '')
        if not st.startswith('STEP_'):
            continue
        api_id = e.get('api_id', '')
        if st == 'STEP_START':
            steps.append({'step': len(steps) + 1, 'api_id': api_id, 'status': 'RUNNING'})
        elif steps and steps[-1]['status'] == 'RUNNING':
            steps[-1]['status'] = st[len('STEP_'):]  # STEP_SUCCESS -> SUCCESS
        else:
            # 无配对 START 的收尾（防御）：单列一步
            steps.append({'step': len(steps) + 1, 'api_id': api_id, 'status': st[len('STEP_'):]})
    return steps


def render_step_checklist(steps: list[dict]) -> str:
    """把步骤清单渲染成人读的 ✓/✗ 文本块。"""
    if not steps:
        return ''
    _mark = {'SUCCESS': '✓'}
    lines = []
    for s in steps:
        mark = _mark.get(s['status'], '✗' if s['status'] not in ('RUNNING',) else '…')
        suffix = '' if s['status'] in ('SUCCESS', 'RUNNING') else f'  [{s["status"]}]'
        lines.append(f"  {s['step']:>2}. {s['api_id']:<32} {mark}{suffix}")
    return '\n'.join(lines)


def reload_internals():
    """热重载内部状态（由 reload_server tool 调用）"""
    from cgi_pipeline.core.capability_registry import reload as _reload_registry
    from cgi_pipeline.core.manifest import reload as _reload_manifest
    _reload_registry()
    _reload_manifest()
