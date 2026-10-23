"""Fence one root operation and restore late-ACK results without replaying writes."""
from __future__ import annotations

import json
import os
import time
import uuid
from functools import wraps
from pathlib import Path

from filelock import FileLock

from cgi_pipeline.contract_values import validate_json_value
from cgi_pipeline.contracts import ApiContractError
from cgi_pipeline.core import task_report_writer, workspace
from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError
from cgi_pipeline.core.runtime_state import process_state, snapshot
from cgi_pipeline.core.task_audit import read_entries, task_state
from cgi_pipeline.core.task_status import TERMINAL_STATUSES


def _runtime(payload):
    descriptor = payload.get('runtime')
    if not isinstance(descriptor, dict):
        raise RuntimePoolError('RUNTIME_BINDING_INVALID', 'Runtime descriptor must be an object.')
    owner = {'project': workspace.workspace_project(payload), 'workspace_id': payload.get('workspace_id')}
    if any(key in descriptor and descriptor[key] != owner[key] for key in owner):
        raise RuntimePoolError('RUNTIME_BINDING_INVALID', 'Runtime binding conflicts with its operation workspace.')
    lease = {**descriptor, 'project': owner['project'],
             'workspace_id': payload.get('workspace_id')}
    for env, field in (('CGI_RUNTIME_ID', 'runtime_id'), ('CGI_RUNTIME_GENERATION', 'generation'),
                       ('CGI_RUNTIME_ROOT', 'root')):
        if not lease.get(field) or os.environ.get(env) != lease[field]:
            raise RuntimePoolError('RUNTIME_PROCESS_MISMATCH', 'Task runtime does not match this worker process.')
    pool = RuntimePool()
    row = pool.validate(lease)
    operation = payload.get('workflow_id') if payload.get('_workspace_subchain') else payload['task_id']
    if row.get('lifecycle') == 'workflow' and row.get('operation_id') != operation:
        raise RuntimePoolError('RUNTIME_BINDING_INVALID', 'Temporary runtime belongs to another operation.')
    worker = row.get('worker')
    if (not pool._match_worker(row, descriptor.get('worker')) or worker['pid'] != os.getpid()
            or not pool._match_worker(row, snapshot(os.getpid()))):
        raise RuntimePoolError('RUNTIME_PROCESS_MISMATCH', 'This execution process is not the assigned runtime worker.')
    return pool, row


def _paths(payload, *, lookup=True):
    identity = (payload['workspace_id'], workspace.workspace_project(payload),
                payload.get('business_task_id') or '')
    run_dir = (workspace._resolve(*identity) if lookup else
               workspace._validate_run_identity(payload['run_dir'], *identity)[0])
    selected = workspace.operation_paths(run_dir, payload['task_id'])
    operation = selected['operation_dir']
    if Path(payload['run_dir']).resolve() != run_dir or Path(payload['operation_dir']).resolve() != operation:
        raise RuntimePoolError('RUNTIME_OPERATION_INVALID', 'Operation does not belong to this workspace and task.')
    for field, expected in (('report_path', selected['report']),
                            ('workspace_report_path', run_dir / 'REPORT.md')):
        if field in payload and Path(payload[field]).resolve() != expected.resolve():
            raise RuntimePoolError('RUNTIME_OPERATION_INVALID', 'Operation report does not belong to this workspace and task.')
    paths = (selected['state'], selected['result'], run_dir / '.info' / 'execution.lock')
    if operation.is_symlink() or any(path.is_symlink() for path in paths):
        raise RuntimePoolError('RUNTIME_OPERATION_INVALID', 'Execution metadata cannot be a file link.')
    with workspace._lock(run_dir):
        state = workspace._read(paths[0])
        if state.get('task_id') != payload['task_id'] or state.get('workspace_id') != payload['workspace_id']:
            raise RuntimePoolError('RUNTIME_OPERATION_INVALID', 'Operation state identity does not match.')
        if (state.get('business_task_id') or '') != (payload.get('business_task_id') or ''):
            raise RuntimePoolError('RUNTIME_OPERATION_INVALID', 'Operation business task identity does not match.')
        if 'project' in state and state['project'] != payload.get('project', 'default'):
            raise RuntimePoolError('RUNTIME_OPERATION_INVALID', 'Operation business project does not match.')
        if 'workspace_project' in state and state['workspace_project'] != workspace.workspace_project(payload):
            raise RuntimePoolError('RUNTIME_OPERATION_INVALID', 'Operation storage namespace does not match.')
        for field in ('run_dir', 'operation_dir', 'report_path', 'workspace_report_path'):
            if field in state and Path(state[field]).resolve() != Path(payload[field]).resolve():
                raise RuntimePoolError('RUNTIME_OPERATION_INVALID', 'Stored operation paths do not match.')
    return run_dir, *paths


def _cached(run_dir, result_path, task_id):
    with workspace._lock(run_dir):
        if not result_path.exists():
            return None
        try:
            result = workspace._read(result_path)
        except (OSError, ValueError) as exc:
            raise RuntimePoolError('RUNTIME_RESULT_INVALID', str(exc)) from exc
        if result.get('task_id') != task_id or result.get('status') not in TERMINAL_STATUSES:
            raise RuntimePoolError('RUNTIME_RESULT_INVALID', 'Stored operation receipt is not a terminal result.')
        validate_json_value(result, 'stored receipt', error_type=ApiContractError)
        return result


def _claim(run_dir, state_path, lease):
    with workspace._lock(run_dir):
        state = workspace._read(state_path)
        if state.get('execution_attempt'):
            raise RuntimePoolError('RUNTIME_PREVIOUS_ATTEMPT_UNKNOWN',
                                   'A previous attempt has no terminal receipt; its effects must not be replayed.')
        state['execution_attempt'] = {
            'status': 'RUNNING', 'attempt_id': uuid.uuid4().hex, 'owner_pid': os.getpid(),
            'started_at': time.time(),
            **{key: lease[key] for key in ('runtime_id', 'generation', 'root')},
        }
        workspace._write(state_path, state)


def _check_previous(run_dir, state_path):
    with workspace._lock(run_dir):
        if workspace._read(state_path).get('execution_attempt'):
            raise RuntimePoolError('RUNTIME_PREVIOUS_ATTEMPT_UNKNOWN',
                                   'A previous attempt has no terminal receipt; its effects must not be replayed.')


def _store(run_dir, state_path, result_path, result):
    validate_json_value(result, 'operation receipt', error_type=ApiContractError)
    with workspace._lock(run_dir):
        # The result is authoritative if a process dies before the state update.
        workspace._write(result_path, result)
        state = workspace._read(state_path)
        attempt = state.setdefault('execution_attempt', {})
        attempt.update(status='TERMINAL', result_status=result['status'], finished_at=time.time())
        workspace._write(state_path, state)


def _failure(payload, exc, *, unknown=False):
    return {**workspace.workspace_result(payload), 'task_id': payload['task_id'],
            'status': 'NEEDS_ATTENTION' if unknown else 'BLOCKED',
            'api_id': payload.get('api_id', 'chain'), 'runtime': payload.get('runtime'),
            'error_code': getattr(exc, 'code', 'RUNTIME_EXECUTION_UNKNOWN'), 'error': str(exc),
            'recovery_hint': ('Inspect the existing scene and outputs; do not replay this operation.' if unknown
                              else 'Verify the exact workspace runtime before submitting a new operation.')}


def _publish(payload, result, audit_path, *, workspace_valid=True):
    if workspace_valid:
        report = payload.get('report_path')
        if report:
            if not Path(report).exists():
                task_report_writer.init_report(report, payload)
            task_report_writer.finalize_report(report, payload, result['status'], error=result.get('error', ''))
        workspace.finish_operation(payload, result['status'])
    path = Path(audit_path(payload['task_id']))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({**result, 'scope': 'task', 'ts': time.time(),
                                 'detail': json.dumps(result, ensure_ascii=False)}, ensure_ascii=False) + '\n')


def _validate_cached_identity(payload, result):
    if ('project' in result and result['project'] != payload.get('project', 'default')) or (
            'workspace_project' in result and result['workspace_project'] != workspace.workspace_project(payload)):
        raise RuntimePoolError('RUNTIME_RESULT_INVALID', 'Stored receipt business/storage project differs.')
    for field in ('workspace_id', 'business_task_id'):
        if (result.get(field) or '') != (payload.get(field) or ''):
            raise RuntimePoolError('RUNTIME_RESULT_INVALID', 'Stored receipt workspace identity does not match.')
    for field in ('run_dir', 'operation_dir', 'report_path', 'workspace_report_path'):
        if field in payload:
            try:
                matches = field in result and Path(result[field]).resolve() == Path(payload[field]).resolve()
            except (TypeError, ValueError, OSError):
                matches = False
            if not matches:
                raise RuntimePoolError('RUNTIME_RESULT_INVALID', 'Stored receipt paths do not match this operation.')


def _restore(payload, result, audit_path, *, lookup=True, trusted_control=False):
    _validate_cached_identity(payload, result)
    if not trusted_control:
        try:
            _runtime(payload)
        except ApiContractError:
            # A late terminal receipt remains readable after its original worker exits.
            return result
    run_dir, path, _, _ = _paths(payload, lookup=lookup)
    with workspace._lock(run_dir):
        state = workspace._read(path)
        attempt = state.setdefault('execution_attempt', {})
        if attempt.get('status') != 'TERMINAL':
            attempt.update(status='TERMINAL', result_status=result['status'], finished_at=time.time())
            workspace._write(path, state)
    current = task_state(read_entries(audit_path(payload['task_id'])))
    statuses = {result['status']}
    if result['status'] == 'SUCCESS':
        statuses.add('CHAIN_SUCCESS')  # Existing chain wrapper's audit projection.
    if (not current.get('terminal') or current.get('status') not in statuses
            or (current.get('error_code') or '') != (result.get('error_code') or '')):
        _publish(payload, result, audit_path)
    return result


def _release_claim(pool, lease, payload, run_dir=None, state_path=None, *, busy):
    try:
        if busy:
            pool.set_idle(lease, payload['task_id'])
        elif isinstance(payload.get('runtime'), dict):
            descriptor = payload['runtime']
            if (descriptor.get('project') != workspace.workspace_project(payload)
                    or descriptor.get('workspace_id') != payload.get('workspace_id')):
                raise RuntimePoolError('RUNTIME_BINDING_INVALID', 'Reservation workspace identity does not match.')
            selected_pool, row = _runtime(payload)
            worker = descriptor.get('worker')
            if not selected_pool._match_worker(row, worker) or process_state(worker, row) != 'LIVE':
                raise RuntimePoolError('RUNTIME_PROCESS_MISMATCH', 'Reservation process ownership is not confirmed.')
            selected_pool.unreserve(descriptor, payload['task_id'], worker=worker)
    except ApiContractError as exc:
        # Keep a canonical stored result intact if ownership changed during shutdown.
        if busy and run_dir is not None:
            with workspace._lock(run_dir):
                state = workspace._read(state_path)
                state['execution_attempt']['resource_cleanup_error'] = {
                    'code': getattr(exc, 'code', 'RUNTIME_CLEANUP_FAILED'), 'error': str(exc)}
                workspace._write(state_path, state)


def _settle_preflight_failure(payload, exc, audit_path):
    """A broken lookup is not permission to trust caller-selected file paths."""
    result = _failure(payload, exc)
    try:
        _runtime(payload)
    except ApiContractError as identity_error:
        result = _failure(payload, identity_error)
        _publish(payload, result, audit_path, workspace_valid=False)
        return result, False
    try:
        run_dir, state_path, result_path, execution_lock = _paths(payload, lookup=False)
    except (ApiContractError, OSError, ValueError, KeyError):
        _publish(payload, result, audit_path, workspace_valid=False)
        return result, False
    with FileLock(str(execution_lock), timeout=-1):
        try:
            # Recheck after waiting: an existing owner may have completed meanwhile.
            _paths(payload, lookup=False)
        except (ApiContractError, OSError, ValueError, KeyError):
            _publish(payload, result, audit_path, workspace_valid=False)
            return result, False
        try:
            saved = _cached(run_dir, result_path, payload['task_id'])
            if saved is not None:
                _validate_cached_identity(payload, saved)
        except (ApiContractError, OSError, ValueError, KeyError):
            # Foreign or corrupt receipt evidence cannot authorize any state repair.
            _publish(payload, result, audit_path, workspace_valid=False)
            return result, False
        if saved is not None:
            return _restore(payload, saved, audit_path, lookup=False), True
        try:
            _check_previous(run_dir, state_path)
        except ApiContractError as previous:
            result = _failure(payload, previous, unknown=True)
        _store(run_dir, state_path, result_path, result)
        _publish(payload, result, audit_path)
        return result, True


def runtime_operation_guard(function, *, audit_path):
    """Wrap the existing business/audit wrapper, preserving unbound legacy calls."""
    @wraps(function)
    def wrapped(self, payload):
        # A relocated terminal operation belongs to its original execution and
        # dead/retired runtime generation. Recover its bytes without touching a
        # lease or repairing historical state in the new storage namespace.
        try:
            from cgi_pipeline.core.workspace_relocation import recover_relocated_execution
            relocated = recover_relocated_execution(payload)
            if relocated is not None:
                return relocated
        except (ApiContractError, OSError, ValueError, KeyError) as exc:
            return _failure(payload, exc, unknown=True)
        if 'runtime' not in payload or payload['runtime'] is None:
            return function(self, payload)
        if payload.get('_workspace_subchain'):
            try:
                _runtime(payload)
            except ApiContractError as exc:
                return _failure(payload, exc)
            return function(self, payload)
        pool = lease = run_dir = state_path = None
        busy = False
        cleanup = False
        try:
            try:
                run_dir, state_path, result_path, execution_lock = _paths(payload)
                saved = _cached(run_dir, result_path, payload['task_id'])
                if saved is not None:
                    _validate_cached_identity(payload, saved)
                    cleanup = True
                    return _restore(payload, saved, audit_path)
            except (ApiContractError, OSError, ValueError, KeyError) as exc:
                result, cleanup = _settle_preflight_failure(payload, exc, audit_path)
                return result
            # Never hold workspace.lock while awaiting an operation or executing DCC code.
            with FileLock(str(execution_lock), timeout=-1):
                saved = _cached(run_dir, result_path, payload['task_id'])
                if saved is not None:
                    _validate_cached_identity(payload, saved)
                    cleanup = True
                    return _restore(payload, saved, audit_path)
                unknown = False
                try:
                    pool, lease = _runtime(payload)
                    cleanup = True
                    _check_previous(run_dir, state_path)
                    _claim(run_dir, state_path, lease)
                    pool.set_busy(lease, payload['task_id'])
                    busy = True
                    try:
                        result = function(self, payload)
                        if (not isinstance(result, dict) or result.get('status') not in TERMINAL_STATUSES
                                or result.get('task_id') != payload['task_id']):
                            raise RuntimeError('Executor returned no terminal operation receipt.')
                        validate_json_value(result, 'operation receipt', error_type=ApiContractError)
                    except Exception as exc:
                        result, unknown = _failure(payload, exc, unknown=True), True
                    _store(run_dir, state_path, result_path, result)
                    if unknown:
                        _publish(payload, result, audit_path)
                    return result
                except ApiContractError as exc:
                    if not cleanup:
                        try:
                            _check_previous(run_dir, state_path)
                        except ApiContractError as previous:
                            exc = previous
                    unknown = getattr(exc, 'code', '') == 'RUNTIME_PREVIOUS_ATTEMPT_UNKNOWN'
                    result = _failure(payload, exc, unknown=unknown)
                    if cleanup:
                        _store(run_dir, state_path, result_path, result)
                    _publish(payload, result, audit_path, workspace_valid=cleanup)
                    return result
                finally:
                    if cleanup:
                        _release_claim(pool, lease, payload, run_dir, state_path, busy=busy)
                        cleanup = False
        finally:
            if cleanup:
                _release_claim(pool, lease, payload, run_dir, state_path, busy=busy)
    return wrapped
