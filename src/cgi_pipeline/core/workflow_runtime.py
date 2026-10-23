"""Durable admission and cleanup for operation-owned workflow runtimes.

The existing broker delivers this controller on the Pipeline worker. It retries
without holding a DCC slot, and never replays a send with unknown acceptance.
Business execution stays in the existing workflow task and operation guard.
"""
from __future__ import annotations

from pathlib import Path
import time

from filelock import FileLock, Timeout

from cgi_pipeline.core import runtime_operation, workspace
from cgi_pipeline.core.runtime_control import RuntimeControl, _audit_path
from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError
from cgi_pipeline.core.runtime_services import RuntimeService
from cgi_pipeline.core.task_audit import read_entries, task_state
from cgi_pipeline.core.task_status import is_success


def _record(payload, values=None):
    run, path, _, _ = runtime_operation._paths(payload)
    with workspace._lock(run):
        state = workspace._read(path)
        current = state.setdefault('workflow_runtime', {})
        if values is not None:
            current.update(values, updated_at=time.time())
            workspace._write(path, state)
        return dict(current)


def _terminal(payload, status, code, message):
    run, state, result, _ = runtime_operation._paths(payload)
    receipt = {**workspace.workspace_result(payload), 'task_id': payload['task_id'],
               'status': status, 'error_code': code, 'error': message}
    runtime_operation._store(run, state, result, receipt)
    runtime_operation._publish(payload, receipt, _audit_path)
    return receipt


def _cleanup(payload, record, pool, service):
    lease = record['lease']
    rows = pool.status(lease['project'], lease['workspace_id'])
    row = next((item for item in rows if RuntimePool._match(item, lease)), None)
    if row is None:
        _record(payload, {'phase': 'RELEASED'})
        return True
    if row.get('lifecycle') != 'workflow' or row.get('operation_id') != payload['task_id']:
        raise RuntimePoolError('RUNTIME_BINDING_INVALID', 'Cleanup is restricted to this workflow operation.')
    if row['active_task'] or row['pending']:
        return False
    result = RuntimeControl(pool, service).release({
        **{key: row[key] for key in ('project', 'workspace_id', 'runtime_id', 'generation')},
        'discard': True})
    _record(payload, {'phase': 'RELEASED' if result.get('released') else 'RETAINED',
                      'reason': result.get('error', ''), 'cleanup': result})
    return True


def _saved_outputs_valid(payload, result):
    if not result.get('save_requested'):
        return True
    saved = result.get('saved_results') or []
    root = Path(payload['run_dir']).resolve()
    return bool(result.get('result_saved') and saved and all(
        Path(item).resolve().is_relative_to(root) and Path(item).is_file()
        and Path(item).stat().st_size > 0 for item in saved))


def attach_progress(result):
    """Project existing operation metadata; business and cleanup are distinct."""
    if not result.get('run_dir') or not result.get('task_id'):
        return result
    try:
        run = workspace._validate_run_identity(result['run_dir'], result['workspace_id'],
                                              None, result.get('business_task_id') or '')[0]
        state = workspace._read(workspace.operation_paths(run, result['task_id'])['state'])
        record = state.get('workflow_runtime')
        if not record or state.get('task_id') != result['task_id']:
            return result
        result['workflow_runtime'] = record
        phase = record.get('phase')
        if is_success(result.get('status')):
            result['business_status'] = result['status']
            if phase == 'RETAINED':
                result.update(status='NEEDS_ATTENTION', error_code='WORKFLOW_RUNTIME_RETAINED',
                              error=record.get('reason', 'Runtime requires inspection.'))
            elif phase != 'RELEASED':
                result.update(status='PROGRESS', terminal=False, detail='Outputs saved; cleaning up workflow runtime.')
        elif phase == 'WAITING' and not result.get('terminal'):
            result.update(status='PROGRESS', detail=record.get('reason', 'Waiting for resources.'))
        elif phase == 'DISPATCHING' and record.get('dispatch_error'):
            result.update(status='NEEDS_ATTENTION', terminal=False,
                          error_code='RUNTIME_SUBMISSION_UNKNOWN',
                          error=record['reason'])
    except (OSError, ValueError, KeyError, RuntimePoolError, workspace.WorkspaceError):
        # Original audit remains available; no filesystem repair in a read call.
        pass
    return result


def advance(payload, *, pool=None, service=None, app=None):
    """Return True when settled; False asks the broker for a later retry."""
    pool = pool or RuntimePool()
    service = service or RuntimeService(pool)
    run, state_path, result_path, execution_lock = runtime_operation._paths(payload)
    lock = state_path.with_name(state_path.stem + '_admission.lock')
    if lock.is_symlink():
        raise RuntimePoolError('RUNTIME_OPERATION_INVALID', 'Admission lock cannot be a link.')
    with FileLock(str(lock), timeout=0):
        record = _record(payload)
        if record.get('phase') in ('RELEASED', 'RETAINED', 'FAILED', 'CANCELLED'):
            return True
        saved = runtime_operation._cached(run, result_path, payload['task_id'])
        if saved is not None:
            runtime_operation._validate_cached_identity(payload, saved)
            if not record.get('lease'):
                return True
            if (not is_success(saved['status'])
                    or not _saved_outputs_valid(payload, saved)):
                _record(payload, {'phase': 'RETAINED', 'reason':
                                  'Failure or incomplete save: scene and diagnostics retained; no replay.'})
                return True
            return _cleanup(payload, record, pool, service)
        if record.get('phase') in ('DISPATCHING', 'DISPATCHED'):
            # Publishing can succeed before a sender crashes. Never send twice.
            lease = record['lease']
            health = service.health(lease)
            if health.get('confirmed_dead') is True:
                _terminal(payload, 'NEEDS_ATTENTION', 'RUNTIME_OWNER_DIED',
                          'Workflow owner died without a result; inspect outputs, do not replay.')
                _record(payload, {'phase': 'RETAINED', 'health': health})
                return True
            return False
        if task_state(read_entries(_audit_path(payload['task_id']))).get('status') == 'CANCEL_REQUESTED':
            _terminal(payload, 'CANCELLED', '', 'Cancelled before workflow dispatch.')
            if record.get('lease'):
                return _cleanup(payload, record, pool, service)
            _record(payload, {'phase': 'CANCELLED'})
            return True
        # Workspaces remain serial, including resident operations. Never hold
        # the execution lock while waiting for resource availability.
        try:
            with FileLock(str(execution_lock), timeout=0):
                lease = pool.acquire_workflow(workspace.workspace_project(payload),
                                              payload['workspace_id'], payload['task_id'])
        except Timeout:
            _record(payload, {'phase': 'WAITING', 'reason': 'WORKSPACE_BUSY'})
            return False
        except RuntimePoolError as exc:
            if exc.code != 'RUNTIME_MEMORY_LOW':
                raise
            _record(payload, {'phase': 'WAITING', 'reason': exc.code})
            return False
        _record(payload, {'phase': 'STARTING', 'lease': lease})
        service.ensure(lease)
        lease = pool.validate(lease)
        payload = dict(payload, runtime=lease, scene_action='open')
        pool.reserve(lease, payload['task_id'])
        _record(payload, {'phase': 'DISPATCHING', 'lease': lease, 'dispatched_at': time.time()})
        if app is None:
            from cgi_pipeline.core.service_manager import get_celery_app
            app = get_celery_app()
        try:
            app.send_task('cgi_pipeline.core.tasks.execute_workflow',
                          args=[payload], task_id=payload['task_id'], queue=lease['queue'])
        except Exception as exc:
            _record(payload, {'dispatch_error': str(exc),
                              'reason': 'Broker acceptance unknown; no automatic resubmission.'})
            return False
        _record(payload, {'phase': 'DISPATCHED'})
        return False


def register_tasks(app):
    @app.task(bind=True, name='cgi_pipeline.core.workflow_runtime.run', max_retries=None)
    def run(self, payload):
        try:
            complete = advance(payload)
        except Timeout:
            complete = False
        except Exception as exc:
            # A failed scheduler must surface a terminal diagnostic, not strand
            # the public operation as SUBMITTED or relaunch its DCC.
            record = _record(payload)
            if record.get('phase') in ('DISPATCHING', 'DISPATCHED'):
                _record(payload, {'controller_error': str(exc)})
                complete = False
            else:
                _terminal(payload, 'NEEDS_ATTENTION', getattr(exc, 'code', 'WORKFLOW_ADMISSION_FAILED'), str(exc))
                _record(payload, {'phase': 'RETAINED' if record.get('lease') else 'FAILED'})
                complete = True
        if not complete:
            raise self.retry(countdown=5, max_retries=None)
        return {'task_id': payload['task_id'], 'workflow_runtime': _record(payload)}
