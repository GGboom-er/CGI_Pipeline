"""Short control operations over existing runtime ownership and host transports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid

from filelock import FileLock, Timeout as LockTimeout

from cgi_pipeline.contracts import ApiContractError
from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError
from cgi_pipeline.core.runtime_services import RuntimeService
from cgi_pipeline.core.runtime_state import process_state, read_identity


def _publication_complete(lease):
    from cgi_pipeline.core.runtime_state import publication_complete
    return publication_complete(lease)


def _identity(lease):
    return {key: lease[key] for key in ('runtime_id', 'generation', 'root')}


def _result(lease, status, code='', error='', **output):
    return dict(status=status, runtime=_identity(lease), error_code=code, error=error, **output)


def _audit_path(task_id):
    from cgi_pipeline.core.bootstrap import cfg
    from cgi_pipeline.core.task_audit import find_audit_file
    root = Path(cfg.PROJECT_ROOT) / 'audit'
    return find_audit_file(task_id, root) or root / f'{task_id}.json'


def _settle_abandoned(lease, audit_path):
    from cgi_pipeline.core import runtime_operation, workspace
    tasks = lease.get('abandoned_tasks') or {}
    if not tasks:
        return
    run_dir = workspace._resolve(lease['workspace_id'], lease['project'])
    for task_id in tasks:
        paths = workspace.operation_paths(run_dir, task_id)
        record = workspace._read(paths['state'])
        payload = dict(record, task_id=task_id, project=record.get('project') or lease['project'],
                       workspace_project=lease['project'], workspace_id=lease['workspace_id'],
                       run_dir=str(run_dir), operation_dir=str(paths['operation_dir']),
                       report_path=str(paths['report']), workspace_report_path=str(run_dir / 'REPORT.md'))
        _, state_path, result_path, _ = runtime_operation._paths(payload)
        saved = runtime_operation._cached(run_dir, result_path, task_id)
        if saved is not None:
            runtime_operation._restore(payload, saved, audit_path, trusted_control=True)
            continue
        result = {**workspace.workspace_result(payload), 'task_id': task_id, 'status': 'NEEDS_ATTENTION',
                  'api_id': record.get('api_id', 'chain'), 'runtime': _identity(lease),
                  'error_code': 'RUNTIME_OWNER_DIED', 'error': 'Runtime worker died; operation effects are unknown.',
                  'recovery_hint': 'Inspect existing scene/output evidence; do not replay the abandoned operation.'}
        runtime_operation._store(run_dir, state_path, result_path, result)
        runtime_operation._publish(payload, result, audit_path)


def _rejected_pending(row, task_id, audit_path):
    """Recognize only the old guard's exact pre-business path refusal."""
    from cgi_pipeline.core import runtime_operation, workspace
    path = Path(audit_path(task_id))
    if path.is_symlink():
        return None
    entries = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if (not entries or any(not isinstance(entry, dict) or entry.get('task_id') != task_id
                           or entry.get('status') not in ('WORKSPACE', 'DISPATCHING', 'BLOCKED')
                           for entry in entries)):
        return None
    terminal = entries[-1]
    dispatches = [entry for entry in entries[:-1] if entry.get('status') == 'DISPATCHING']
    if (len(dispatches) != 1 or terminal.get('scope') != 'task' or terminal.get('status') != 'BLOCKED'
            or terminal.get('error_code') != 'RUNTIME_OPERATION_INVALID'
            or terminal.get('error') != 'Operation does not belong to this workspace and task.'):
        return None
    dispatch = dispatches[0]
    for source in (dispatch, terminal):
        descriptor = source.get('runtime')
        if (not RuntimePool._match(row, descriptor)
                or not RuntimePool._match_worker(row, descriptor.get('worker'))
                or source.get('workspace_id') != row['workspace_id']):
            return None
    if dispatch.get('queue') != row['queue'] or terminal.get('api_id') != dispatch.get('api_id'):
        return None
    for field in ('business_task_id', 'run_dir', 'operation_dir', 'report_path', 'workspace_report_path'):
        if field not in dispatch or field not in terminal:
            return None
        if (dispatch[field] != terminal[field] if field == 'business_task_id' else
                Path(dispatch[field]).resolve() != Path(terminal[field]).resolve()):
            return None
    payload = dict(dispatch, project=row['project'])
    run_dir, state_path, result_path, execution_lock = runtime_operation._paths(payload)
    record = workspace._read(state_path)
    if record.get('status') not in ('SUBMITTED', 'BLOCKED') or record.get('api_id') != dispatch.get('api_id'):
        return None
    result = {key: value for key, value in terminal.items() if key not in ('scope', 'ts', 'detail')}
    saved = runtime_operation._cached(run_dir, result_path, task_id)
    if saved is not None and saved != result:
        return None
    attempt = record.get('execution_attempt')
    if attempt and (saved is None or attempt.get('attempt_id') or attempt.get('status') != 'TERMINAL'
                    or attempt.get('result_status') != 'BLOCKED'):
        return None
    return payload, result, (run_dir, state_path, result_path, execution_lock)


class RuntimeControl:
    def __init__(self, pool=None, service=None, *, rpc=None, publication_complete=None, audit_path=None):
        self.pool = pool or RuntimePool()
        self.service = service or RuntimeService(self.pool)
        self.rpc = rpc or self._rpc
        self.publication_complete = publication_complete or _publication_complete
        self.audit_path = audit_path or _audit_path

    @staticmethod
    def _rpc(lease, discard):
        from cgi_pipeline.core.service_manager import get_celery_app
        result = get_celery_app().send_task('cgi_pipeline.core.tasks.execute_runtime_control',
                                          args=[lease, discard], queue=lease['queue'])
        return result.get(timeout=30, disable_sync_subtasks=False)

    def status(self, params):
        checked_at = time.time()
        rows = self.pool.status(params.get('project', ''), params.get('workspace_id', ''))
        result = []
        for row in rows:
            try:
                health = (dict(state='LOST', checked_at=checked_at, dirty_state='UNKNOWN')
                          if row['state'] == 'LOST' else self.service.health(row))
            except RuntimePoolError as exc:
                # The ownership snapshot may race a completed release/reassignment.
                health = dict(state='UNKNOWN', checked_at=time.time(), dirty_state='UNKNOWN',
                              error_code=exc.code, error=str(exc))
            result.append({'lease': row, 'health': health})
        return {'pool_root': str(self.pool.root.resolve()), 'max_slots': self.pool.max_slots,
                'slots': result, 'checked_at': checked_at}

    @staticmethod
    def _unknown(lease, error, code='RUNTIME_CONTROL_UNKNOWN'):
        return _result(lease, 'NEEDS_ATTENTION', code, error, released=False)

    def _recover(self, row):
        complete, reason = self.publication_complete(row)
        row = self.pool.begin_recovery(row, explicit_discard=True, confirmed_worker_dead=True)
        _settle_abandoned(row, self.audit_path)
        if not complete:
            return self._unknown(row, reason, 'RUNTIME_PUBLICATION_UNKNOWN')
        return self._stop(row, recovered=True)

    def _stop(self, row, *, recovered=False, discarded=False, hosts=None):
        complete, reason = self.publication_complete(row)
        if not complete:
            return self._unknown(row, reason, 'RUNTIME_PUBLICATION_UNKNOWN')
        if self.service.stop(row) is not True:
            return self._unknown(row, 'Owned process stop is not confirmed.')
        complete, reason = self.publication_complete(row)
        if not complete:
            return self._unknown(row, reason, 'RUNTIME_PUBLICATION_UNKNOWN')
        if recovered:
            self.pool.mark_lost(row, confirmed_dead=True)
            return _result(row, 'SUCCESS', released=True, recovered=True, binding_state='LOST')
        self.pool.finish_release(row, True)
        return _result(row, 'SUCCESS', released=True, recovered=False, discarded=discarded, hosts=hosts or [])

    def _settle_rejected_pending(self, row):
        from cgi_pipeline.core import runtime_operation
        if row['active_task'] or row['state'] != 'IDLE':
            return row
        for task_id in tuple(row['pending']):
            try:
                candidate = _rejected_pending(row, task_id, self.audit_path)
                if candidate is None:
                    continue
                _, _, paths = candidate
                with FileLock(str(paths[3]), timeout=0):
                    current = self.pool.validate(row)
                    worker = self.service._worker(current)
                    if (current['active_task'] or current['state'] != 'IDLE'
                            or task_id not in current['pending']
                            or not self.pool._match_worker(current, worker)
                            or process_state(worker, current, self.service.process_factory) != 'LIVE'):
                        continue
                    candidate = _rejected_pending(current, task_id, self.audit_path)
                    if candidate is None:
                        continue
                    payload, result, (run_dir, state_path, result_path, _) = candidate
                    runtime_operation._store(run_dir, state_path, result_path, result)
                    runtime_operation._publish(payload, result, self.audit_path)
                    self.pool.unreserve(current, task_id, worker=worker)
            except (ApiContractError, OSError, ValueError, KeyError, TypeError, LockTimeout):
                # Missing, foreign, running or unknown evidence retains admission fencing.
                continue
        return self.pool.validate(row)

    def _release(self, row, discard):
        worker = self.service._worker(row)
        state = process_state(worker, row, self.service.process_factory) if worker else 'UNKNOWN'
        if state == 'DEAD':
            if not discard:
                return _result(row, 'BLOCKED', 'RUNTIME_DISCARD_REQUIRED',
                               'Dead runtime recovery requires explicit discard.', released=False)
            return self._recover(row)
        if state != 'LIVE':
            return self._unknown(row, f'Worker identity is not confirmed live or dead: {state}.')
        row = self._settle_rejected_pending(row)
        row = self.pool.begin_release(row)
        if discard:
            # A previous read-only control RPC may be stuck in its host query.
            # Explicit discard is authorized exact-owned stop, not another queue item.
            return self._stop(row, discarded=True)
        try:
            result = self.rpc(row, discard)
            if not isinstance(result, dict) or result.get('runtime') != _identity(row):
                return self._unknown(row, 'Runtime control reply has no matching process identity.')
            if (result.get('status') == 'BLOCKED' and result.get('safe_to_resume') is True
                    and result.get('hosts_released') is False):
                self.pool.finish_release(row, False)
                return _result(row, 'BLOCKED', result.get('error_code', 'RUNTIME_HOST_DIRTY'),
                               result.get('error', 'Host dirty state refuses release.'), released=False,
                               hosts=result.get('hosts', []))
            if result.get('status') != 'SUCCESS' or result.get('hosts_released') is not True:
                return self._unknown(row, result.get('error') or 'Host shutdown outcome is unknown.')
            return self._stop(row, hosts=result.get('hosts', []))
        except Exception as exc:
            # A late RPC or stop may still be executing. Keep admission fenced.
            return self._unknown(row, str(exc))

    def release(self, params):
        self.pool._key(params['project'], params['workspace_id'])
        rows = self.pool.status(params['project'], params['workspace_id'])
        row = next((row for row in rows if all(row[key] == params[key]
                   for key in ('runtime_id', 'generation'))), None)
        if row is None:
            raise RuntimePoolError('RUNTIME_GENERATION_MISMATCH', 'Workspace runtime identity does not match.')
        root = Path(row['root'])
        root.mkdir(parents=True, exist_ok=True)
        lock = root / 'release.lock'
        if lock.is_symlink():
            raise RuntimePoolError('RUNTIME_IDENTITY_UNKNOWN', 'Runtime release lock cannot be a link.')
        try:
            with FileLock(str(lock), timeout=0):
                current = self.pool.validate(row)
                try:
                    return self._release(current, bool(params.get('discard', False)))
                except RuntimePoolError:
                    raise
                except Exception as exc:
                    return self._unknown(current, str(exc))
        except LockTimeout as exc:
            raise RuntimePoolError('RUNTIME_CONTROL_BUSY', 'Another release owns this runtime lifecycle.') from exc


def _host_state(worker, host, lease):
    code = ("import maya.cmds as cmds\nresult = {'dirty': bool(cmds.file(query=True, modified=True)), "
            "'scene': cmds.file(query=True, sceneName=True) or ''}" if host == 'maya' else
            "import bpy\nresult = {'dirty': bool(bpy.data.is_dirty), 'scene': bpy.data.filepath or '', "
            "'background': bool(bpy.app.background)}")
    try:
        reply = worker.run_api({'task_id': f'release-probe-{uuid.uuid4().hex[:12]}',
                               'api_id': 'maya.asset.exec_code' if host == 'maya' else 'blender.asset.blender_exec_code',
                               'api_params': {'code': code, 'description': 'Read fresh host dirty state before release'},
                               'api_context': {'execution_mode': 'background', 'project': lease['project']},
                               # A host IPC timeout kills that host. Only the outer
                               # RPC may time out; a dirty read must never discard it.
                               'timeout': 0})
        if not isinstance(reply, dict) or reply.get('status') != 'SUCCESS':
            raise ValueError('Host transport did not confirm the query.')
        receipt = reply.get('detail', reply)
        receipt = json.loads(receipt) if isinstance(receipt, str) else receipt
        state = (receipt.get('output') or {}).get('result') if receipt.get('status') == 'SUCCESS' else None
        if not isinstance(state, dict) or type(state.get('dirty')) is not bool or not isinstance(state.get('scene'), str):
            raise ValueError('Host dirty query has no confirmed bool dirty/string scene result.')
        if host == 'blender':
            if type(state.get('background')) is not bool:
                raise ValueError('Blender dirty query has no confirmed background mode.')
            if state['background']:
                state = {**state, 'native_dirty': state['dirty'], 'dirty': 'UNKNOWN',
                         'clean_state': 'UNKNOWN', 'dirty_basis': 'native_notifier_unconsumed'}
        return {'host': host, **state}
    except Exception as exc:
        return {'host': host, 'dirty': 'UNKNOWN', 'error': str(exc)}


def release_hosts(lease, discard=False):
    """Run serially inside the assigned Celery worker; never create a DCC host."""
    pool = RuntimePool()
    row = pool.validate(lease)
    for env, field in [('CGI_RUNTIME_ID', 'runtime_id'), ('CGI_RUNTIME_GENERATION', 'generation'),
                       ('CGI_RUNTIME_ROOT', 'root')]:
        if os.environ.get(env) != row[field]:
            raise RuntimePoolError('RUNTIME_PROCESS_MISMATCH', 'Control task reached a foreign runtime worker.')
    if row['state'] != 'DRAINING' or row['active_task'] or row['pending']:
        raise RuntimePoolError('RUNTIME_BUSY', 'Host release requires a drained exact runtime.')
    complete, reason = _publication_complete(row)
    if not complete:
        return _result(row, 'NEEDS_ATTENTION', 'RUNTIME_PUBLICATION_UNKNOWN', reason,
                       hosts_released=False, safe_to_resume=False)
    from cgi_pipeline.core import dcc_factory
    selected = []
    for host, entry in list(dcc_factory._warm_pool.items()):
        worker = entry.get('worker')
        runtime = getattr(worker, 'runtime', None)
        if host in ('maya', 'blender') and runtime and runtime.enabled and (
                runtime.runtime_id, runtime.generation, str(runtime.root)) == (
                row['runtime_id'], row['generation'], row['root']):
            selected.append((host, entry, worker))
    states = [_host_state(worker, host, row) for host, _, worker in selected]
    known_ids = {getattr(worker, 'worker_id', None) for _, _, worker in selected}
    try:
        for path in (Path(row['root']) / 'hosts').glob('*.json'):
            record = read_identity(path, row)
            if record and record.get('worker_id') not in known_ids and process_state(record, row) != 'DEAD':
                states.append({'host': record.get('host', 'unknown'), 'dirty': 'UNKNOWN',
                               'error': 'Published host has no process-local warm pool owner.'})
    except RuntimePoolError as exc:
        states.append({'host': 'unknown', 'dirty': 'UNKNOWN', 'error': str(exc)})
    if not discard and any(state['dirty'] is not False for state in states):
        return _result(row, 'BLOCKED', 'RUNTIME_HOST_DIRTY', 'Dirty or unknown host state refuses release.',
                       hosts=states, hosts_released=False, safe_to_resume=True)
    try:
        for host, entry, worker in selected:
            worker.shutdown()
            if worker.process and worker.process.poll() is None:
                raise RuntimeError(f'{host} host shutdown is not confirmed.')
            if dcc_factory._warm_pool.get(host) is entry:
                dcc_factory._warm_pool.pop(host)
    except Exception as exc:
        return _result(row, 'NEEDS_ATTENTION', 'RUNTIME_HOST_STOP_UNKNOWN', str(exc),
                       hosts=states, hosts_released=False, safe_to_resume=False)
    return _result(row, 'SUCCESS', hosts=states, hosts_released=True, safe_to_resume=False)
