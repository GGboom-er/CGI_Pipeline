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

load_dotenv(REPOSITORY_ROOT / '.env')

PROJECT_ROOT = REPOSITORY_ROOT
AUDIT_DIR = PROJECT_ROOT / 'audit'

from cgi_pipeline.core.capability_registry import get_executor, resolve_api_id
from cgi_pipeline.core.manifest import get_executor_queue
from cgi_pipeline.core.task_status import TERMINAL_STATUSES, business_status
from cgi_pipeline.catalog import snapshot
from cgi_pipeline.contracts import ApiContractError, validate_params, validate_receipt
from cgi_pipeline.core.workspace import WorkspaceError, start_operation, finish_operation, workspace_result
from cgi_pipeline.core.runtime_pool import RuntimePoolError
from cgi_pipeline.server.runtime_dispatch import prepare_dispatch, send_task, dispatch_error, execute_control
from cgi_pipeline.server.http_lifecycle import lifecycle

RECEIPT_INLINE_LIMIT_BYTES = 64 * 1024


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


def _append_submission_audit(task_id: str, api_id: str, queue: str, status: str = 'SUBMITTED', detail: str = '', metadata: dict | None = None) -> Path:
    """记录提交阶段，避免排队任务或提交中断伪装成 NOT_FOUND。"""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = _find_audit_file(task_id)
    if audit_path is None:
        directory = AUDIT_DIR / time.strftime('%Y-%m-%d') if queue == 'foreground' else AUDIT_DIR
        directory.mkdir(parents=True, exist_ok=True)
        audit_path = directory / f'{task_id}.json'
    entry = {
        'task_id': task_id,
        'api_id': api_id,
        'status': status,
        'ts': time.time(),
        'detail': detail or (f'任务已结束: {status}' if status in TERMINAL_STATUSES
                             else f'任务已提交至 {queue}，等待 Worker 开始执行'),
        'queue': queue,
        **(metadata or {}),
    }
    with audit_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return audit_path


def _bind_workspace(payload: dict, kind: str, *, foreground: bool = False) -> dict | None:
    try:
        if payload.get('scene_action', 'continue') not in ('continue', 'open'):
            raise WorkspaceError('WORKSPACE_SCENE_ACTION_INVALID', 'scene_action must be continue or open.')
        if foreground and payload.get('scene_action') == 'open':
            raise WorkspaceError('WORKSPACE_FOREGROUND_OPEN_UNSUPPORTED', 'Foreground calls operate on the selected live scene; scene_action=open is background-only.')
        metadata = start_operation(payload, kind, foreground=foreground)
        if metadata:
            _append_submission_audit(payload['task_id'], payload.get('api_id', kind), 'foreground' if foreground else '',
                                     'WORKSPACE' if metadata.get('workspace_report_path') else 'TASK_LINK', json.dumps(metadata, ensure_ascii=False))
            payload.setdefault('api_context', {}).update(metadata)
    except (WorkspaceError, OSError, ValueError) as exc:
        return {'task_id': payload['task_id'], 'status': 'ERROR',
                'error_code': getattr(exc, 'code', 'WORKSPACE_STATE_INVALID'), 'error': str(exc)}
    return None


def _workspace_submission(function):
    from functools import wraps
    @wraps(function)
    def wrapped(*args, **kwargs):
        payload = kwargs.get('payload') or args[-1]
        try:
            result = function(*args, **kwargs)
        except RuntimePoolError as exc:
            result = dispatch_error(payload, exc)
        metadata = workspace_result(payload)
        if metadata.get('workspace_report_path') and result.get('status') != 'SUBMITTED' and not result.get('workspace_report_path'):
            from cgi_pipeline.core import task_report_writer as writer
            if not Path(payload['report_path']).exists():
                writer.init_report(payload['report_path'], payload)
                writer.finalize_report(payload['report_path'], payload, result.get('status', 'ERROR'), error=result.get('error', ''))
            finish_operation(payload, result.get('status', 'ERROR'))
            _append_submission_audit(payload['task_id'], payload.get('api_id', payload.get('workflow_id', 'chain')), '',
                                     result.get('status', 'ERROR'), json.dumps({**metadata, **result}, ensure_ascii=False, default=str))
        return {**metadata, **result}
    return wrapped


def _run_foreground_workspace(function, payload: dict, **kwargs):
    """Keep foreground scene behavior while retaining an explicit workspace report."""
    metadata = workspace_result(payload)
    if not metadata.get('workspace_report_path'):
        try:
            result = function(payload, **kwargs)
            result = _normalize_execution_result(result)
        except Exception as exc:
            result = {'task_id': payload['task_id'], 'status': 'ERROR', 'error': str(exc)}
        _append_submission_audit(payload['task_id'], payload.get('api_id', 'batch'), 'foreground',
                                 result['status'], result.get('error', ''),
                                 metadata={**metadata, **{key: result[key] for key in ('error', 'error_code', 'error_type') if key in result}})
        return {**metadata, **result}
    metadata['scene_session'] = {'status': 'FOREGROUND', 'staged': False, 'opened': False,
                                 'foreground_port': payload.get('api_context', {}).get('foreground_port')}
    from cgi_pipeline.core import task_report_writer as writer
    report = payload['report_path']
    writer.init_report(report, payload)
    try:
        result = function(payload, **kwargs)
    except Exception as exc:
        result = {'task_id': payload['task_id'], 'status': 'ERROR', 'error': str(exc)}
    result = _normalize_execution_result(result)
    raw = result.get('detail', result)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = result
    rows = raw.get('results', [raw]) if isinstance(raw, dict) else [result]
    steps = payload.get('api_chain') or [{'api_id': payload.get('api_id', ''), 'parameters': payload.get('api_params', {})}]
    for index, row in enumerate(rows):
        step = steps[index] if index < len(steps) else {}
        api_id = step.get('api_id', payload.get('api_id', 'batch'))
        status = row.get('status', result.get('status', 'ERROR')) if isinstance(row, dict) else result.get('status', 'ERROR')
        receipt = writer.extract_receipt(row, api_id, status)
        writer.upsert_step_finished(report, {'step_index': index, 'step_total': len(steps),
                                            'api_id': api_id, 'parameters': step.get('parameters', {})},
                                    receipt, worker_status=status)
    writer.finalize_report(report, payload, result.get('status', 'ERROR'), error=result.get('error', ''))
    finish_operation(payload, result.get('status', 'ERROR'))
    _append_submission_audit(payload['task_id'], payload.get('api_id', 'batch'), 'foreground',
                             result.get('status', 'ERROR'), json.dumps(result, ensure_ascii=False, default=str),
                             metadata={**metadata, **{key: result[key] for key in ('error', 'error_code', 'error_type') if key in result}})
    return {**metadata, **result}


def _normalize_execution_result(result: dict) -> dict:
    """Preserve diagnostics from business receipts or transport envelopes alike."""
    nested = result.get('receipt') or result.get('detail')
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except ValueError:
            nested = None
    nested = nested if isinstance(nested, dict) else {}
    diagnostics = {key: result.get(key) or nested[key] for key in ('error', 'error_code', 'error_type')
                   if result.get(key) or nested.get(key)}
    return {**result, **diagnostics, 'status': business_status(result)}


def _dispatch_foreground(function, payload: dict, *, wait: bool, **kwargs) -> dict:
    """Share one async dispatch path across atomic APIs and literal batches."""
    if wait:
        return _run_foreground_workspace(function, payload, **kwargs)
    metadata = workspace_result(payload)
    _append_submission_audit(payload['task_id'], payload.get('api_id', 'batch'), 'foreground', metadata=metadata)
    lifecycle.spawn_thread(_run_foreground_workspace, function, payload, **kwargs)
    return {**metadata, 'task_id': payload['task_id'], 'status': 'SUBMITTED',
            'api_id': payload.get('api_id', 'batch'),
            'recovery_hint': 'Recover this task_id with get_task_result; do not resubmit.'}


def _validate_service_execution(execution_mode: str, foreground_port: int | None, *,
                                invalid_mode_code: str = 'API_CONTRACT_ERROR',
                                spec: dict | None = None) -> dict | None:
    """Reject invalid service routing before workspace or transport side effects."""
    if execution_mode == 'local':
        return {
            'status': 'ERROR', 'error_code': 'SERVICE_EXECUTION_MODE_UNSUPPORTED',
            'error': 'execution_mode=local cannot run through the CGI service.',
            'recovery_hint': 'Run api.execute_local(...) inside the selected DCC host, or choose background/foreground service execution.',
        }
    if execution_mode not in ('background', 'foreground'):
        return {
            'status': 'ERROR', 'error_code': invalid_mode_code,
            'error': 'execution_mode must be background or foreground.',
            'recovery_hint': 'Choose background, or foreground with an explicit foreground_port.',
        }
    if execution_mode == 'foreground' and not foreground_port:
        return {
            'status': 'ERROR', 'error_code': 'FOREGROUND_PORT_REQUIRED',
            'error': 'foreground execution requires an explicit foreground_port.',
            'recovery_hint': 'Select the target DCC session and pass its foreground_port; no session was selected automatically.',
        }
    if spec is not None and execution_mode not in spec['execution_modes']:
        return {
            'status': 'ERROR', 'error_code': 'API_CONTRACT_ERROR',
            'error': f"{spec['api_id']} does not support execution_mode={execution_mode}",
            'recovery_hint': 'Read api_help for supported execution modes.',
        }
    return None


@_workspace_submission
def _submit_api_to_celery(api_id: str, payload: dict) -> dict:
    """Submit one catalog API through the existing Worker/report path.

    API execution deliberately reuses ``execute_api_operation`` as the transport
    task.  The DCC adapter dispatches ``api_id`` to ``cgi_pipeline.execution``; no second
    queue, worker, or workflow implementation is created.
    """
    requested_api_id = str(api_id or '')
    selected_snapshot = snapshot()
    context = dict(payload.get('context') or {})
    execution_mode = context.get('execution_mode', 'background')
    execution_error = _validate_service_execution(execution_mode, context.get('foreground_port'))
    if execution_error:
        return {'api_id': requested_api_id, **execution_error}
    expected = context.get('expected_catalog_version')
    if expected is not None and expected != selected_snapshot[1]:
        return {
            'status': 'ERROR', 'error_code': 'CATALOG_VERSION_STALE',
            'catalog_version': selected_snapshot[1],
            'error': 'Capability catalog changed before submission.',
            'recovery_hint': 'Refresh api_help and submit again; no DCC was started.',
        }
    context['expected_catalog_version'] = selected_snapshot[1]
    try:
        api_id = resolve_api_id(requested_api_id, catalog_snapshot=selected_snapshot)
        spec = selected_snapshot[0][api_id]
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

    execution_error = _validate_service_execution(execution_mode, context.get('foreground_port'), spec=spec)
    if execution_error:
        return {'task_id': task_id, 'api_id': api_id, **execution_error}

    payload['api_params'] = normalized_params
    payload['api_context'] = {
        **context,
        'task_id': task_id,
        'execution_mode': execution_mode,
        'project': payload.get('project', context.get('project', '')),
        'asset_name': payload.get('asset_name', context.get('asset_name', '')),
        'source_path': payload.get('source_path', context.get('source_path', '')),
        'run_dir': context.get('run_dir', ''),
        'workspace_id': payload.get('workspace_id', ''),
        'business_task_id': payload.get('business_task_id', ''),
    }
    if payload.get('workspace_project') is not None:
        payload['api_context']['workspace_project'] = payload['workspace_project']
    payload.pop('params', None)
    payload.pop('context', None)
    if spec.get('execution_strategy', 'worker') == 'control':
        return execute_control(payload, spec, selected_snapshot, _append_submission_audit)
    workspace_error = _bind_workspace(payload, 'api', foreground=execution_mode == 'foreground')
    if workspace_error:
        return workspace_error
    if execution_mode == 'foreground':
        executor = spec['executor']
        if executor == 'maya':
            from cgi_pipeline.server.foreground_client import submit_foreground_task
        elif executor == 'blender':
            from cgi_pipeline.hosts.blender.foreground_client import submit_foreground_task
        elif executor == 'ue':
            from cgi_pipeline.hosts.ue.foreground_client import submit_foreground_task
        else:
            return {
                'task_id': task_id,
                'status': 'SUBMIT_FAILED',
                'api_id': api_id,
                'error': f'Foreground mode is not supported for executor: {executor}',
            }
        return _dispatch_foreground(submit_foreground_task, payload,
                                    wait=bool(context.get('sync', True)), sync=True)

    executor = spec['executor']
    queue = prepare_dispatch(payload, executor, _ensure_worker)
    from cgi_pipeline.core.service_manager import start_redis
    if not start_redis():
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'api_id': api_id,
            'error': 'Redis 未运行且启动失败',
            'recovery_hint': '请检查 CGI 运行时服务状态。',
        }
    try:
        from cgi_pipeline.core.service_manager import get_celery_app
        app = get_celery_app()
        _append_submission_audit(
            task_id, api_id, queue, status='DISPATCHING',
            detail=f'准备提交 API 至 {queue} 队列',
            metadata=workspace_result(payload),
        )
        send_task(app, 'cgi_pipeline.core.tasks.execute_api_operation', payload, queue)
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'api_id': api_id,
            'message': f'API 已提交至 {queue} 队列',
        }
    except RuntimePoolError:
        raise
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

@_workspace_submission
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
    selected_snapshot = snapshot()
    api_context = dict(payload.get('api_context') or {})
    expected = api_context.get('expected_catalog_version')
    if expected is not None and expected != selected_snapshot[1]:
        return {
            'task_id': task_id, 'status': 'ERROR', 'error_code': 'CATALOG_VERSION_STALE',
            'catalog_version': selected_snapshot[1],
            'error': 'Capability catalog changed before submission.',
            'recovery_hint': 'Refresh api_help and submit again; no DCC was started.',
        }
    payload['api_context'] = {**api_context, 'expected_catalog_version': selected_snapshot[1]}
    from cgi_pipeline.core.schemas import ApiChainPayloadSchema
    try:
        validated = ApiChainPayloadSchema.model_validate(
            payload, context={'catalog_snapshot': selected_snapshot})
        api_chain = validated.api_chain
        if len({selected_snapshot[0][step['api_id']]['executor'] for step in api_chain}) != 1:
            raise ValueError('All chain APIs must use the same executor.')
        if any(selected_snapshot[0][step['api_id']].get('execution_strategy') == 'control' for step in api_chain):
            raise ValueError('Runtime control APIs are standalone calls; they cannot run inside a scene batch.')
    except (ValueError, KeyError) as exc:
        return {'task_id': task_id, 'status': 'ERROR', 'error_code': 'BATCH_CONTRACT_ERROR', 'error': str(exc)}
    first_step = api_chain[0] if api_chain else {}
    first_api_id = resolve_api_id(str(first_step.get('api_id') or ''), catalog_snapshot=selected_snapshot)
    first_step['api_id'] = first_api_id
    payload['api_chain'] = api_chain
    executor = selected_snapshot[0][first_api_id]['executor']
    workspace_error = _bind_workspace(payload, 'batch' if payload.get('batch_mode') else 'chain')
    if workspace_error:
        return workspace_error
    queue = prepare_dispatch(payload, executor, _ensure_worker)

    try:
        from cgi_pipeline.core.service_manager import get_celery_app
        _app = get_celery_app()
        _append_submission_audit(task_id, 'chain', queue, status='DISPATCHING', metadata=workspace_result(payload))
        send_task(_app, 'cgi_pipeline.core.tasks.execute_api_chain', payload, queue)
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'dispatched': True,
            'steps': len(api_chain),
            'message': f'链式任务已提交至 {queue}，共 {len(api_chain)} 步',
        }
    except RuntimePoolError:
        raise
    except Exception as e:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'dispatched': False,
            'error': str(e),
            'recovery_hint': '请检查 Redis 和 Celery Worker 状态',
        }


@_workspace_submission
def _submit_workflow(payload: dict) -> dict:
    """提交工作流到唯一 CGI 队列；workflow 内部顺序执行各 DCC 段。"""
    task_id = f"wf-{uuid.uuid4().hex[:8]}"
    payload['task_id'] = task_id
    payload['submitted_at'] = time.time()

    queue = 'cgi_queue'

    # 先校验工作流定义，再只确保一个 CGI Worker。
    from cgi_pipeline.core.workflow_engine import compile_workflow, load_workflow, validate_workflow_inputs
    try:
        workflow = compile_workflow(load_workflow(payload['workflow_id']), payload.get('extra_params'))
        payload['extra_params'] = workflow['extra_params']
        validate_workflow_inputs(workflow, payload)
        payload['_workflow_plan'] = workflow
        workflow_state = {'effective_parameters': workflow['effective_parameters'],
                          'save_requested': workflow['save_requested'],
                          'result_saved': False, 'saved_results': []}
    except ApiContractError as exc:
        return {'task_id': task_id, 'status': 'ERROR', 'error_code': 'WORKFLOW_INPUT_INVALID',
                'workflow_id': payload.get('workflow_id', ''), 'error': str(exc),
                'recovery_hint': 'Bind the list_workflows input_contract/template before submitting; no worker was started.'}
    except Exception as exc:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'workflow_id': payload.get('workflow_id', ''),
            'error': f'工作流定义不可用: {exc}',
        }

    workspace_error = _bind_workspace(payload, 'workflow')
    if workspace_error:
        return workspace_error
    queue = prepare_dispatch(payload, 'cgi', _ensure_worker)

    try:
        from cgi_pipeline.core.service_manager import get_celery_app
        _app = get_celery_app()
        _append_submission_audit(task_id, payload['workflow_id'], queue, status='DISPATCHING', metadata={**workspace_result(payload), **workflow_state})
        send_task(_app, 'cgi_pipeline.core.tasks.execute_workflow', payload, queue)
        return {
            'task_id': task_id,
            'status': 'SUBMITTED',
            'workflow_id': payload['workflow_id'],
            **workflow_state,
            'message': f'工作流已提交至 {queue}',
        }
    except RuntimePoolError:
        raise
    except Exception as e:
        return {
            'task_id': task_id,
            'status': 'SUBMIT_FAILED',
            'error': str(e),
        }


def _attach_api_receipts(result: dict, entries: list[dict], audit_path: Path) -> None:
    """Extract business results before clipping logs, with one shared byte budget."""
    remaining = RECEIPT_INLINE_LIMIT_BYTES
    rows = []
    for index, entry in enumerate(entries):
        detail = entry.get('detail', '')
        if not isinstance(detail, (str, dict)):
            continue
        try:
            raw = json.JSONDecoder().raw_decode(detail.lstrip())[0] if isinstance(detail, str) else detail
        except (ValueError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        nested = isinstance(raw.get('results'), list)
        if entry.get('status') == 'BATCH_RESULT' or nested:
            result['batch'] = {key: raw[key] for key in (
                'status', 'completed', 'requested', 'atomic', 'rolled_back', 'elapsed_sec'
            ) if key in raw}
        for child_index, candidate in enumerate(raw['results'] if nested else [raw]):
            try:
                receipt = validate_receipt(candidate, api_id=candidate['api_id'], api_version=candidate['api_version'])
            except (ValueError, TypeError, KeyError, ApiContractError):
                continue
            location = {'entry_index': index, **({'result_index': child_index} if nested else {})}
            size = len(json.dumps(receipt, ensure_ascii=False).encode('utf-8'))
            row = {'api_id': receipt['api_id'], 'status': receipt['status'], **location}
            if size <= remaining:
                row['receipt'] = receipt
                remaining -= size
            else:
                row['receipt_ref'] = {
                    'audit_path': str(audit_path), **location,
                    'utf8_bytes': size, 'inline_limit_bytes': RECEIPT_INLINE_LIMIT_BYTES,
                }
            rows.append(row)
    if len(rows) == 1:
        row = rows[0]
        # An earlier successful step must not replace a later transport failure.
        if row['status'] != 'SUCCESS' or result['status'] in ('SUCCESS', 'CHAIN_SUCCESS'):
            key = 'receipt' if 'receipt' in row else 'receipt_ref'
            result[key] = row[key]
            if result['status'] in ('SUCCESS', 'CHAIN_SUCCESS') and not result.get('batch'):
                result['status'] = row['status']
                result['terminal'] = row['status'] in TERMINAL_STATUSES
        else:
            result['step_receipts'] = rows
    elif rows:
        result['step_receipts'] = rows


def _read_audit(task_id: str) -> dict:
    """读取任务状态：Redis 进度 hash → Celery PROGRESS → 审计文件。"""
    from cgi_pipeline.core.task_audit import read_adoption_route, read_relocation_route
    relocation_route = read_relocation_route(task_id, AUDIT_DIR)
    if relocation_route is not None:
        from cgi_pipeline.core.workspace_relocation import recover_relocation
        return recover_relocation(task_id, relocation_route)
    route = read_adoption_route(task_id, AUDIT_DIR)
    if route is not None:
        from cgi_pipeline.core.workspace_adoption import recover_adoption
        return recover_adoption(task_id, route)
    progress_result = None
    # 1. 优先从 Redis 进度 hash 读取实时状态（新架构）
    try:
        from cgi_pipeline.core.progress import get_latest_state
        state = get_latest_state(task_id)
        if state and state.get('event_type'):
            evt = state.get('event_type', '')
            if evt not in ('workflow.done', 'workflow.error'):
                progress_result = {
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
        if not progress_result and res.state == 'PROGRESS' and isinstance(res.info, dict):
            progress_result = {
                'task_id': task_id,
                'status': 'PROGRESS',
                'meta': res.info,
            }
    except Exception:
        pass

    audit_path = _find_audit_file(task_id)

    if audit_path is None:
        return progress_result or {
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
    from cgi_pipeline.core.task_audit import task_state
    latest = task_state(entries)
    result = {
        'task_id': task_id,
        'status': latest.get('status', 'UNKNOWN'),
        'terminal': latest['terminal'],
        'detail': latest.get('detail', ''),
        'step': latest.get('step', -1),
        'api_id': latest.get('api_id', ''),
        'timestamp': latest.get('ts', 0),
        'total_entries': len(entries),
        'entries': entries,  # 暴露全部审计条目，供调用方遍历链步骤
    }
    for entry in entries:
        metadata = entry
        if entry.get('status') in ('WORKSPACE', 'TASK_LINK'):
            try:
                metadata = json.loads(entry['detail'])
            except (ValueError, TypeError):
                continue
        for key in ('workspace_id', 'business_task_id', 'project', 'workspace_project', 'run_dir', 'report_path', 'workspace_report_path', 'operation_dir', 'scene_session', 'runtime', 'error_code', 'error', 'error_type', 'recovery_hint',
                    'effective_parameters', 'save_requested', 'result_saved', 'saved_results'):
            if key in metadata:
                result[key] = metadata[key]
    if progress_result and not latest['terminal'] and latest.get('status') != 'CANCEL_REQUESTED':
        return {**{key: result[key] for key in ('workspace_id', 'business_task_id', 'project', 'workspace_project', 'run_dir', 'report_path', 'workspace_report_path', 'operation_dir', 'scene_session', 'runtime',
                                              'effective_parameters', 'save_requested', 'result_saved', 'saved_results') if key in result},
                **progress_result, 'audit_path': str(audit_path)}

    # 终态瘦身：全量审计可达数十万字符，会撑爆 MCP 调用方；只留末 3 条并截断长 detail。
    # 完整日志仍在审计文件（audit_path 随结果返回），需要全量直接读文件。
    if latest['terminal']:
        _attach_api_receipts(result, entries, audit_path)
        def _clip(text, cap=800):
            text = text if isinstance(text, str) else str(text)
            return text if len(text) <= cap else text[:cap] + f'…[截断,共{len(text)}字符,全量见 audit_path]'
        result['detail'] = _clip(result['detail'])
        result['entries'] = [{**e, 'detail': _clip(e.get('detail', ''))} for e in entries[-3:]]
        result['audit_path'] = str(audit_path)

    # 任务到达终态时，尽量回填沙盒报告路径。成功和失败都应有报告。
    if latest['terminal']:
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

    from cgi_pipeline.core.workspace_relocation import project_recovery
    return project_recovery(result)


def get_task_result(task_id: str) -> dict:
    """Return a task state and its normalized API receipt when available."""
    result = _read_audit(task_id)
    from cgi_pipeline.core.workflow_runtime import attach_progress
    result = attach_progress(result)
    if result.get('recovery_source') in ('native_adoption_commit', 'native_relocation_commit'):
        return result
    if result.get('status') not in TERMINAL_STATUSES:
        return result

    steps = collect_workflow_steps(task_id)
    if steps:
        result['steps'] = steps
        result['step_checklist'] = render_step_checklist(steps)

    manifest_path, manifest = _load_workflow_manifest(task_id, result)
    if manifest:
        result['workflow_id'] = manifest.get('workflow_id', '')
        result['manifest_path'] = str(manifest_path)
        outputs = manifest.get('outputs', {})
        result['outputs'] = dict(outputs) if isinstance(outputs, dict) else {}
        for key in ('effective_parameters', 'save_requested', 'result_saved', 'saved_results'):
            if key in manifest.get('inputs', {}):
                result.setdefault(key, manifest['inputs'][key])
        if not result.get('report_path'):
            reports = manifest.get('reports', [])
            if isinstance(reports, list) and reports:
                result['report_path'] = str(reports[0])
        from cgi_pipeline.core.workspace_relocation import project_recovery
        result = project_recovery(result)
        post_compare = _summarize_post_compare(result.get('effective_outputs', result['outputs']))
        if post_compare:
            result['post_compare'] = post_compare
    from cgi_pipeline.core.workspace_relocation import project_recovery
    return project_recovery(result)


def _manifest_for_artifact(task_id: str, artifact_path: str) -> tuple[Path | None, dict]:
    from cgi_pipeline.core import workspace
    raw_path = str(artifact_path or '').strip()
    if not raw_path:
        return None, {}
    path = Path(raw_path)
    directory = path if path.is_dir() else path.parent
    checked_workspaces = set()
    for parent in (directory, *list(directory.parents)[:3]):
        run_dir = None
        if parent.name == '.info':
            run_dir = parent.parent
        elif parent.parent.name == 'operations' and parent.parent.parent.name == '.info':
            run_dir = parent.parents[2]
        elif (parent / '.info').is_dir():
            run_dir = parent
        if run_dir is not None and run_dir not in checked_workspaces:
            checked_workspaces.add(run_dir)
            selected = workspace.operation_paths(run_dir, task_id)['manifest']
            if selected.is_file():
                try:
                    manifest = workspace._read(selected)
                except (OSError, ValueError) as exc:
                    raise workspace.WorkspaceError('WORKSPACE_STATE_INVALID',
                                                   f'Cannot read workflow manifest: {selected}: {exc}') from exc
                if manifest.get('wf_id') != task_id:
                    raise workspace.WorkspaceError('WORKSPACE_STATE_INVALID',
                                                   f'Workflow manifest identity does not match {task_id}.')
                return selected, manifest
        # Older non-operation runs stored a bare manifest beside their artifacts.
        manifest_path = parent / 'manifest.json'
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(manifest, dict) and manifest.get('wf_id') == task_id:
            return manifest_path, manifest
    return None, {}


def _load_workflow_manifest(task_id: str, result: dict) -> tuple[Path | None, dict]:
    """Locate the existing workflow manifest from receipt/audit paths without a project-wide scan."""
    if result.get('report_path'):
        found = _manifest_for_artifact(task_id, result['report_path'])
        if found[1]:
            return found

    audit_path = Path(result.get('audit_path') or '')
    if not audit_path.is_file():
        audit_path = _find_audit_file(task_id) or Path()
    if not audit_path.is_file():
        return None, {}
    try:
        lines = audit_path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return None, {}
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        detail = entry.get('detail', '') if isinstance(entry, dict) else ''
        if not isinstance(detail, str) or ('"sandbox"' not in detail and '"report_path"' not in detail):
            continue
        try:
            detail_data = json.loads(detail.split(' [mem=')[0])
        except json.JSONDecodeError:
            continue
        if not isinstance(detail_data, dict):
            continue
        for key in ('sandbox', 'report_path'):
            if detail_data.get(key):
                found = _manifest_for_artifact(task_id, detail_data[key])
                if found[1]:
                    return found
    return None, {}


def _summarize_post_compare(outputs: dict) -> dict:
    candidates = [
        str(value)
        for key, value in outputs.items()
        if isinstance(value, str)
        and ('post_compare' in value.lower() or key == 'verify.output_path')
    ]
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        compare = payload.get('compare', {}) if isinstance(payload, dict) else {}
        if not isinstance(compare, dict):
            continue
        count_fields = (
            'paired', 'only_a', 'only_b', 'merge_groups', 'split_groups',
            'pairing_groups', 'target_only_dags',
        )
        counts = {
            field: len(compare.get(field, []))
            if isinstance(compare.get(field, []), (list, dict)) else 0
            for field in count_fields
        }
        actions: dict[str, int] = {}
        for group in compare.get('pairing_groups', []):
            action = group.get('action', '') if isinstance(group, dict) else ''
            if action:
                actions[action] = actions.get(action, 0) + 1
        return {
            'path': str(path),
            'schema_version': payload.get('schema_version', ''),
            'total_issues': compare.get('total_issues'),
            'labels': {'a': compare.get('label_a', ''), 'b': compare.get('label_b', '')},
            'counts': counts,
            'actions': dict(sorted(actions.items())),
        }
    return {}


def _find_audit_file(task_id: str, audit_dir=None):
    """定位某 task_id 的审计文件（按日期分目录优先，扁平回退）。"""
    from cgi_pipeline.core.task_audit import find_audit_file
    return find_audit_file(task_id, audit_dir if audit_dir is not None else AUDIT_DIR)


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
