"""Bind standard submissions to a workspace-affine, bounded execution lane."""
from __future__ import annotations

from pathlib import Path

from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError
from cgi_pipeline.core.runtime_services import RuntimeService
from cgi_pipeline.core.workspace import _lock, _read, _write, workspace_project


def assign_runtime(payload, *, pool=None, service=None):
    if not payload.get('workspace_id') or not payload.get('run_dir'):
        return None
    pool = pool or RuntimePool()
    service = service or RuntimeService(pool)
    state_path = Path(payload['run_dir']) / '.info' / 'run_state.json'
    with _lock(Path(payload['run_dir'])):
        state = _read(state_path)
        if (state.get('scene_hosts') and not state.get('runtime')
                and payload.get('scene_action', 'continue') != 'open'):
            raise RuntimePoolError('RUNTIME_LEGACY_BOUND',
                'Workspace owns a legacy scene. Continue through its original client, '
                'or explicitly open a saved source in the isolated runtime.')
    lease = pool.acquire(workspace_project(payload), payload['workspace_id'],
                         rebuild=payload.get('scene_action') == 'open')
    try:
        service.ensure(lease)
    except RuntimePoolError as exc:
        health = service.health(lease) if exc.code == 'RUNTIME_LOST' else {}
        if health.get('confirmed_dead') is not True:
            raise
        # Reconcile every accepted/active operation before freeing its resource
        # record. A later explicit open must not strand old task queries RUNNING.
        from cgi_pipeline.core.runtime_control import RuntimeControl
        reconciled = RuntimeControl(pool, service).release({
            **{key: lease[key] for key in ('project', 'workspace_id', 'runtime_id', 'generation')},
            'discard': True})
        if reconciled.get('released') is not True:
            raise RuntimePoolError('RUNTIME_RECOVERY_UNKNOWN',
                                   reconciled.get('error') or 'Dead runtime reconciliation is incomplete.') from exc
        if payload.get('scene_action') != 'open':
            raise RuntimePoolError('RUNTIME_LOST', 'Runtime died; explicitly open a saved scene to rebuild.') from exc
        lease = pool.acquire(workspace_project(payload), payload['workspace_id'], rebuild=True)
        service.ensure(lease)
    lease = pool.validate(lease)
    payload['runtime'] = lease
    payload.setdefault('api_context', {})['runtime'] = lease
    with _lock(Path(payload['run_dir'])):
        state = _read(state_path)
        state['runtime'] = lease
        _write(state_path, state)
    return lease['queue']


def prepare_dispatch(payload, executor, ensure_legacy):
    """Resolve admission before sending; no new hidden waiting queue."""
    from cgi_pipeline.core.manifest import get_executor_queue
    # The caller resolves executor from its validated catalog snapshot. Pipeline
    # APIs use the existing file/service worker, not a DCC scene lane, even when
    # their business workspace also contains an old or current scene binding.
    temporary_workflow = (payload.get('_workflow_plan', {}).get('runtime_lifecycle') == 'temporary')
    if executor == 'pipeline' or temporary_workflow:
        payload.pop('runtime', None)
        if isinstance(payload.get('api_context'), dict):
            payload['api_context'].pop('runtime', None)
        queue = None
    else:
        queue = assign_runtime(payload)
    if queue:
        return queue
    ok, message = ensure_legacy(executor)
    if not ok:
        error = RuntimePoolError('LEGACY_WORKER_UNAVAILABLE', message)
        error.status = 'SUBMIT_FAILED'
        raise error
    return get_executor_queue(executor)


def send_task(app, name, payload, queue):
    if (name == 'cgi_pipeline.core.tasks.execute_workflow'
            and payload.get('_workflow_plan', {}).get('runtime_lifecycle') == 'temporary'):
        try:
            return app.send_task('cgi_pipeline.core.workflow_runtime.run', args=[payload],
                                 task_id=payload['task_id'] + '-runtime', queue=queue)
        except Exception as exc:
            error = RuntimePoolError('RUNTIME_SUBMISSION_UNKNOWN',
                                     f'Workflow admission acceptance is unknown; retain task_id: {exc}')
            error.status, error.dispatched = 'NEEDS_ATTENTION', None
            raise error from exc
    lease = payload.get('runtime')
    if lease:
        RuntimePool().reserve(lease, payload['task_id'])
    # Broker failure can occur after acceptance. Keep the reservation until its
    # outcome is reconciled; never declare an unknown send undispatched/replayable.
    try:
        return app.send_task(name, args=[payload], task_id=payload['task_id'], queue=queue)
    except Exception as exc:
        error = RuntimePoolError('RUNTIME_SUBMISSION_UNKNOWN',
            f'Broker acceptance is unknown; retain task_id and inspect its audit: {exc}')
        error.status = 'NEEDS_ATTENTION'
        error.dispatched = None
        raise error from exc


def dispatch_error(payload, exc):
    return {'task_id': payload['task_id'], 'status': getattr(exc, 'status', 'ERROR'),
            'error_code': getattr(exc, 'code', 'RUNTIME_DISPATCH_FAILED'),
            'error': str(exc), 'dispatched': getattr(exc, 'dispatched', False)}


def execute_control(payload, spec, selected_snapshot, append_audit):
    """Use the canonical handler/receipt/audit, without a workspace or DCC slot."""
    import json
    from cgi_pipeline.execution import execute_api
    task_id = payload['task_id']
    receipt = execute_api(spec['api_id'], payload['api_params'],
                          payload['api_context'], catalog_snapshot=selected_snapshot)
    result = {'task_id': task_id, **receipt}
    append_audit(task_id, spec['api_id'], 'control', receipt['status'],
                 json.dumps(receipt, ensure_ascii=False),
                 metadata={key: receipt[key] for key in ('error_code', 'error') if receipt.get(key)})
    return result
