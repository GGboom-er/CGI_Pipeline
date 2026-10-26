from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import yaml

from cgi_pipeline.core import runtime_control as control, workspace
from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError
from cgi_pipeline.core.task_audit import read_entries, task_state
from cgi_pipeline.contracts import validate_spec
from test_workspace import root


def ready(pool, wid='one'):
    lease = pool.acquire('default', wid)
    identity = {key: lease[key] for key in ('runtime_id', 'generation', 'root')}
    return pool.set_worker_info(lease, dict(identity, pid=1234, create_time=1.25, cmdline=['worker']))


def params(lease, discard=False):
    return {key: lease[key] for key in ('project', 'workspace_id', 'runtime_id', 'generation')} | {'discard': discard}


def reply(lease, status='SUCCESS', **extras):
    return {'status': status, 'runtime': {key: lease[key] for key in ('runtime_id', 'generation', 'root')},
            'hosts_released': status == 'SUCCESS', 'safe_to_resume': status == 'BLOCKED', **extras}


class FakeService:
    def __init__(self):
        self.worker_state, self.stop_result, self.stopped = 'LIVE', True, []
        self.process_factory = self

    def _worker(self, lease):
        return lease['worker']

    def health(self, lease):
        return {'state': self.worker_state, 'dirty_state': 'UNKNOWN', 'checked_at': 123.5}

    def stop(self, lease):
        self.stopped.append(lease['runtime_id'])
        return self.stop_result


@pytest.fixture
def unit(tmp_path, monkeypatch):
    # Pin the slot budget: the host .env (loaded on import) may configure another one.
    monkeypatch.setenv('CGI_RUNTIME_MAX_SLOTS', '3')
    pool = RuntimePool(tmp_path / 'pool', allow_external_root=True, memory_probe=lambda: 64 * 1024 ** 3)
    service = FakeService()
    monkeypatch.setattr(control, 'process_state', lambda identity, lease, factory: factory.worker_state)
    calls = []
    def rpc(lease, discard):
        calls.append((lease['runtime_id'], discard))
        return reply(lease)
    instance = control.RuntimeControl(pool, service, rpc=rpc, publication_complete=lambda lease: (True, 'complete'))
    return SimpleNamespace(pool=pool, service=service, calls=calls, instance=instance)


def test_status_reads_three_full_busy_slots_without_rpc_or_admission(unit):
    for wid in ('a', 'b', 'c'):
        lease = ready(unit.pool, wid)
        unit.pool.reserve(lease, 'task-' + wid)
        unit.pool.set_busy(lease, 'task-' + wid)
    result = unit.instance.status({})
    assert len(result['slots']) == 3 and result['checked_at'] > 0
    assert result['pool_root'] == str(unit.pool.root.resolve()) and result['max_slots'] == 3
    assert all(row['health']['dirty_state'] == 'UNKNOWN' for row in result['slots'])
    assert unit.calls == [] and unit.service.stopped == []


def test_status_retains_unknown_snapshot_when_health_races_reassignment(unit):
    ready(unit.pool)
    def changed(row):
        raise RuntimePoolError('RUNTIME_GENERATION_MISMATCH', 'snapshot generation was released')
    unit.service.health = changed
    result = unit.instance.status({})
    assert result['slots'][0]['health']['state'] == 'UNKNOWN'
    assert result['slots'][0]['health']['error_code'] == 'RUNTIME_GENERATION_MISMATCH'
    assert unit.calls == []


def test_clean_release_stops_only_a_and_preserves_b(unit):
    a, b = ready(unit.pool, 'a'), ready(unit.pool, 'b')
    result = unit.instance.release(params(a))
    assert result['status'] == 'SUCCESS' and result['released'] is True
    assert unit.service.stopped == [a['runtime_id']]
    assert unit.pool.status() == [unit.pool.validate(b)]


@pytest.mark.parametrize('active', [False, True])
def test_live_busy_release_refuses_even_with_discard(unit, active):
    lease = ready(unit.pool)
    unit.pool.reserve(lease, 'task')
    if active:
        unit.pool.set_busy(lease, 'task')
    with pytest.raises(RuntimePoolError) as failure:
        unit.instance.release(params(lease, True))
    assert failure.value.code == 'RUNTIME_BUSY'
    assert unit.calls == [] and unit.service.stopped == []


def test_concurrent_release_uses_nonblocking_per_slot_lifecycle_lock(unit):
    lease = ready(unit.pool)
    entered, release = threading.Event(), threading.Event()
    def rpc(row, discard):
        entered.set()
        assert release.wait(5)
        return reply(row)
    unit.instance.rpc = rpc
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(unit.instance.release, params(lease))
        assert entered.wait(5)
        try:
            with pytest.raises(RuntimePoolError) as failure:
                unit.instance.release(params(lease))
            assert failure.value.code == 'RUNTIME_CONTROL_BUSY'
        finally:
            release.set()
        assert first.result(timeout=5)['released'] is True
    assert unit.service.stopped == [lease['runtime_id']]


@pytest.mark.parametrize('mode', ['rpc_timeout', 'stop_unknown', 'bad_rpc'])
def test_unknown_release_keeps_draining_and_never_reopens_admission(unit, mode):
    lease = ready(unit.pool)
    if mode == 'rpc_timeout':
        def timeout(*args):
            raise TimeoutError('RPC timed out')
        unit.instance.rpc = timeout
    elif mode == 'stop_unknown':
        unit.service.stop_result = False
    else:
        unit.instance.rpc = lambda *args: {'status': 'SUCCESS'}
    result = unit.instance.release(params(lease))
    assert result['status'] == 'NEEDS_ATTENTION' and result['released'] is False
    assert unit.pool.validate(lease)['state'] == 'DRAINING'
    with pytest.raises(RuntimePoolError):
        unit.pool.reserve(lease, 'late')


def test_confirmed_dirty_refusal_safely_returns_idle(unit):
    lease = ready(unit.pool)
    unit.instance.rpc = lambda row, discard: reply(row, 'BLOCKED', error_code='RUNTIME_HOST_DIRTY')
    assert unit.instance.release(params(lease))['status'] == 'BLOCKED'
    assert unit.pool.validate(lease)['state'] == 'IDLE'
    assert unit.service.stopped == []


def test_contradictory_refusal_cannot_reopen_admission(unit):
    lease = ready(unit.pool)
    unit.instance.rpc = lambda row, discard: reply(row, 'BLOCKED', hosts_released=True)
    assert unit.instance.release(params(lease))['status'] == 'NEEDS_ATTENTION'
    assert unit.pool.validate(lease)['state'] == 'DRAINING'


def test_rpc_timeout_can_be_followed_by_explicit_discard_without_another_rpc(unit):
    lease = ready(unit.pool)
    def timeout(*args):
        raise TimeoutError('previous dirty query remains blocked')
    unit.instance.rpc = timeout
    assert unit.instance.release(params(lease))['status'] == 'NEEDS_ATTENTION'
    unit.instance.rpc = lambda *args: pytest.fail('discard must not queue behind the stuck control query')
    result = unit.instance.release(params(lease, True))
    assert result['status'] == 'SUCCESS' and result['discarded'] is True
    assert unit.pool.status() == [] and unit.service.stopped == [lease['runtime_id']]


def test_stop_requires_complete_publication_after_confirmed_process_exit(unit):
    lease = ready(unit.pool)
    unit.instance.publication_complete = lambda row: (
        not unit.service.stopped, 'publication became incomplete')
    result = unit.instance.release(params(lease, True))
    assert result['status'] == 'NEEDS_ATTENTION'
    assert unit.pool.validate(lease)['state'] == 'DRAINING'


def test_missing_identity_is_unknown_even_if_process_probe_would_report_dead(unit):
    lease = ready(unit.pool)
    unit.service.worker_state = 'DEAD'
    unit.service._worker = lambda row: None
    assert unit.instance.release(params(lease, True))['status'] == 'NEEDS_ATTENTION'
    assert unit.service.stopped == []


def test_dead_worker_with_incomplete_publication_never_frees_slot(unit):
    lease = ready(unit.pool)
    unit.service.worker_state = 'DEAD'
    unit.instance.publication_complete = lambda row: (False, 'pending host launch')
    result = unit.instance.release(params(lease, True))
    row = unit.pool.validate(lease)
    assert result['status'] == 'NEEDS_ATTENTION' and row['state'] == 'DRAINING'
    assert row['publication_unknown'] and unit.service.stopped == []


def test_dead_worker_requires_discard_and_unknown_identity_is_not_death(unit):
    lease = ready(unit.pool)
    for state, discard in [('DEAD', False), ('UNKNOWN', True), ('FOREIGN', True)]:
        unit.service.worker_state = state
        result = unit.instance.release(params(lease, discard))
        assert result['status'] in ('BLOCKED', 'NEEDS_ATTENTION')
        assert unit.service.stopped == []


@pytest.mark.parametrize('layout', ['flat', 'legacy'])
def test_recovery_settles_dead_active_operation_and_preserves_lost_binding(root, unit, layout):
    payload = {'task_id': 'dead-operation', 'project': 'default', 'asset_name': 'test',
               'api_id': 'maya.scene.selection.get', 'scene_action': 'open'}
    workspace.start_operation(payload, 'api')
    if layout == 'legacy':
        from test_runtime_operation import as_legacy
        as_legacy(payload)
    lease = ready(unit.pool, payload['workspace_id'])
    unit.pool.reserve(lease, payload['task_id'])
    unit.pool.set_busy(lease, payload['task_id'])
    paths = workspace.operation_paths(payload['run_dir'], payload['task_id'])
    path = paths['state']
    record = workspace._read(path)
    record['execution_attempt'] = {'status': 'RUNNING'}
    workspace._write(path, record)
    audit = root / 'audit'
    audit.mkdir()
    audit_file = audit / 'dead-operation.json'
    audit_file.write_text(json.dumps({'status': 'TASK_RUNNING', 'scope': 'task'}) + '\n')
    unit.instance.audit_path = lambda task_id: audit / f'{task_id}.json'
    unit.service.worker_state = 'DEAD'
    result = unit.instance.release(params(lease, True))
    assert result['status'] == 'SUCCESS' and result['recovered'] is True
    assert unit.pool.status('default', payload['workspace_id'])[0]['state'] == 'LOST'
    assert workspace._read(path)['status'] == 'NEEDS_ATTENTION'
    assert task_state(read_entries(audit_file))['status'] == 'NEEDS_ATTENTION'
    assert workspace._read(paths['result'])['error_code'] == 'RUNTIME_OWNER_DIED'


def test_begin_recovery_requires_explicit_discard_and_confirmed_death(unit):
    lease = ready(unit.pool)
    unit.pool.reserve(lease, 'pending')
    unit.pool.set_busy(lease, 'pending')
    for discard, dead in [(False, True), (True, False)]:
        with pytest.raises(RuntimePoolError):
            unit.pool.begin_recovery(lease, explicit_discard=discard, confirmed_worker_dead=dead)
    row = unit.pool.begin_recovery(lease, explicit_discard=True, confirmed_worker_dead=True)
    assert row['state'] == 'DRAINING' and not row['active_task'] and not row['pending']
    assert 'pending' in row['abandoned_tasks'] and row['publication_unknown']


@pytest.mark.parametrize('layout', ['flat', 'legacy'])
def test_recovery_preserves_terminal_cached_result_and_repairs_its_task_audit(root, unit, layout):
    from cgi_pipeline.core import runtime_operation
    payload = {'task_id': 'already-done', 'project': 'default', 'asset_name': 'test',
               'api_id': 'maya.scene.selection.get', 'scene_action': 'open'}
    workspace.start_operation(payload, 'api')
    if layout == 'legacy':
        from test_runtime_operation import as_legacy
        as_legacy(payload)
    lease = ready(unit.pool, payload['workspace_id'])
    unit.pool.reserve(lease, payload['task_id'])
    unit.pool.set_busy(lease, payload['task_id'])
    paths = workspace.operation_paths(payload['run_dir'], payload['task_id'])
    saved = {**workspace.workspace_result(payload), 'task_id': payload['task_id'],
             'status': 'SUCCESS', 'original_receipt': {'value': 17}}
    runtime_operation._store(Path(payload['run_dir']), paths['state'], paths['result'], saved)
    audit = root / 'audit'
    audit.mkdir()
    (audit / 'already-done.json').write_text(json.dumps({'status': 'TASK_RUNNING', 'scope': 'task'}) + '\n')
    unit.instance.audit_path = lambda task_id: audit / f'{task_id}.json'
    unit.service.worker_state = 'DEAD'
    assert unit.instance.release(params(lease, True))['released'] is True
    assert workspace._read(paths['result']) == saved
    assert task_state(read_entries(audit / 'already-done.json'))['status'] == 'SUCCESS'


class Host:
    def __init__(self, lease, dirty=False, *, background=False):
        self.runtime = SimpleNamespace(enabled=True, **{key: lease[key] for key in ('root', 'runtime_id', 'generation')})
        self.dirty, self.queries, self.closed, self.dead = dirty, [], 0, False
        self.background = background
        self.process = SimpleNamespace(poll=lambda: 0 if self.dead else None)

    def run_api(self, payload):
        self.queries.append(payload)
        return {'status': 'SUCCESS', 'detail': json.dumps({'status': 'SUCCESS', 'output': {
            'result': {'dirty': self.dirty, 'scene': 'managed-scene', 'background': self.background}}})}

    def shutdown(self):
        self.closed += 1
        self.dead = True


def hosts(unit, monkeypatch):
    from cgi_pipeline.core import dcc_factory
    lease = unit.pool.begin_release(ready(unit.pool))
    monkeypatch.setattr(control, 'RuntimePool', lambda: unit.pool)
    monkeypatch.setattr(control, '_publication_complete', lambda row: (True, 'complete'))
    for key, field in [('CGI_RUNTIME_ID', 'runtime_id'), ('CGI_RUNTIME_GENERATION', 'generation'),
                       ('CGI_RUNTIME_ROOT', 'root')]:
        monkeypatch.setenv(key, lease[field])
    pool = {}
    monkeypatch.setattr(dcc_factory, '_warm_pool', pool)
    return lease, pool


def test_worker_queries_every_host_before_dirty_refusal_and_discard_is_explicit(unit, monkeypatch):
    lease, pool = hosts(unit, monkeypatch)
    maya, blender = Host(lease), Host(lease, True)
    pool.update(maya={'worker': maya}, blender={'worker': blender})
    denied = control.release_hosts(lease, False)
    assert denied['status'] == 'BLOCKED' and denied['safe_to_resume'] is True
    assert maya.queries and blender.queries and not maya.closed and not blender.closed
    assert control.release_hosts(lease, True)['hosts_released'] is True
    assert maya.closed == blender.closed == 1 and pool == {}
    assert maya.queries[0]['api_id'] == 'maya.asset.exec_code'
    assert blender.queries[0]['api_id'] == 'blender.asset.blender_exec_code'
    assert all(query['timeout'] == 0 for query in maya.queries + blender.queries)


@pytest.mark.parametrize('native_dirty', [False, True])
def test_headless_native_dirty_never_authorizes_default_release(unit, monkeypatch, native_dirty):
    lease, pool = hosts(unit, monkeypatch)
    blender = Host(lease, native_dirty, background=True)
    pool['blender'] = {'worker': blender}
    denied = control.release_hosts(lease, False)
    assert denied['status'] == 'BLOCKED' and denied['hosts_released'] is False
    state = denied['hosts'][0]
    assert state['dirty'] == 'UNKNOWN' and state['clean_state'] == 'UNKNOWN'
    assert state['native_dirty'] is native_dirty
    assert state['dirty_basis'] == 'native_notifier_unconsumed'
    assert 'bpy.app.background' in blender.queries[0]['api_params']['code']
    assert blender.closed == 0
    assert control.release_hosts(lease, True)['hosts_released'] is True
    assert blender.closed == 1 and pool == {}


def test_verified_headless_save_success_does_not_supply_discard_authorization(unit, monkeypatch):
    from test_blender_save_scene import FakeBpy, context, save_scene
    repository = unit.pool.root.parent
    monkeypatch.setattr(save_scene, 'REPOSITORY_ROOT', repository)
    bpy = FakeBpy(background=True)
    bpy.keep_dirty = True
    saved = save_scene.execute({'save_path': str(repository / 'projects' / 'snapshot.blend')}, context(bpy))
    assert saved['status'] == 'SUCCESS' and saved['output']['artifact_verified'] is True
    lease, pool = hosts(unit, monkeypatch)
    blender = Host(lease, saved['output']['native_dirty'], background=True)
    pool['blender'] = {'worker': blender}
    unit.instance.rpc = control.release_hosts
    denied = unit.instance.release(params(lease))
    assert denied['status'] == 'BLOCKED' and denied['released'] is False
    assert denied['hosts'][0]['clean_state'] == 'UNKNOWN'
    assert unit.pool.validate(lease)['state'] == 'IDLE'
    assert blender.closed == 0 and unit.service.stopped == []
    unit.instance.rpc = lambda *args: pytest.fail('explicit discard does not repeat the native probe')
    discarded = unit.instance.release(params(lease, True))
    assert discarded['released'] is True and discarded['discarded'] is True
    assert unit.service.stopped == [lease['runtime_id']]


def test_blender_query_without_confirmed_background_mode_cannot_release(unit, monkeypatch):
    lease, pool = hosts(unit, monkeypatch)
    blender = Host(lease)
    blender.run_api = lambda payload: {'status': 'SUCCESS', 'detail': json.dumps({
        'status': 'SUCCESS', 'output': {'result': {'dirty': False, 'scene': 'managed-scene'}}})}
    pool['blender'] = {'worker': blender}
    denied = control.release_hosts(lease, False)
    assert denied['status'] == 'BLOCKED' and denied['hosts'][0]['dirty'] == 'UNKNOWN'
    assert blender.closed == 0


def test_worker_never_shuts_foreign_runtime_entry_or_accepts_foreign_env(unit, monkeypatch):
    lease, pool = hosts(unit, monkeypatch)
    own, foreign = Host(lease), Host(dict(lease, generation='foreign'))
    pool.update(maya={'worker': own}, blender={'worker': foreign})
    assert control.release_hosts(lease, True)['hosts_released'] is True
    assert own.closed == 1 and foreign.closed == 0 and pool['blender']['worker'] is foreign
    monkeypatch.setenv('CGI_RUNTIME_GENERATION', 'foreign')
    with pytest.raises(RuntimePoolError):
        control.release_hosts(lease, True)


def test_worker_unknown_dirty_query_refuses_without_killing_or_closing_host(unit, monkeypatch):
    lease, pool = hosts(unit, monkeypatch)
    own = Host(lease)
    def query_failure(payload):
        assert payload['timeout'] == 0
        raise RuntimeError('dirty query unavailable')
    own.run_api = query_failure
    pool['maya'] = {'worker': own}
    result = control.release_hosts(lease, False)
    assert result['status'] == 'BLOCKED' and result['hosts'][0]['dirty'] == 'UNKNOWN'
    assert own.closed == 0 and pool['maya']['worker'] is own


def test_worker_incomplete_launch_publication_is_not_clean_or_safe_to_resume(unit, monkeypatch):
    lease, pool = hosts(unit, monkeypatch)
    own = Host(lease)
    pool['maya'] = {'worker': own}
    monkeypatch.setattr(control, '_publication_complete', lambda row: (False, 'uncommitted launch'))
    result = control.release_hosts(lease, False)
    assert result['status'] == 'NEEDS_ATTENTION' and result['safe_to_resume'] is False
    assert own.queries == [] and own.closed == 0


def test_canonical_status_handler_reports_empty_pool_identity(unit, monkeypatch):
    from cgi_pipeline.capabilities.pipeline.runtime import handler
    from cgi_pipeline.contracts import ApiContext, validate_receipt
    monkeypatch.setattr(handler, 'RuntimeControl', lambda: unit.instance)
    result = handler.status({}, ApiContext(executor='pipeline'))
    validate_receipt(result, api_id='pipeline.runtime.status', api_version='1.0.0')
    assert result['status'] == 'SUCCESS'
    assert result['output']['slots'] == [] and result['output']['max_slots'] == 3
    assert result['output']['pool_root'] == str(unit.pool.root.resolve())


def test_control_manifests_validate_without_generated_catalog_edit():
    directory = Path(control.__file__).parents[1] / 'capabilities' / 'pipeline' / 'runtime'
    for name in ('status', 'release'):
        spec = validate_spec(yaml.safe_load((directory / name / 'capability.yaml').read_text('utf-8')))
        assert spec['execution_strategy'] == 'control' and spec['executor'] == 'pipeline'
        assert spec['api_id'] == f'pipeline.runtime.{name}'


def rejected_pending(root, unit, layout='flat'):
    from cgi_pipeline.core import runtime_operation
    from test_runtime_operation import as_flat, as_legacy
    payload = {'task_id': 'rejected-operation', 'project': 'default', 'asset_name': 'test',
               'api_id': 'maya.asset.exec_code', 'scene_action': 'open'}
    workspace.start_operation(payload, 'api')
    (as_flat if layout == 'flat' else as_legacy)(payload)
    lease = ready(unit.pool, payload['workspace_id'])
    unit.pool.reserve(lease, payload['task_id'])
    payload['runtime'] = lease
    audit = root / 'audit'
    audit.mkdir()
    unit.instance.audit_path = lambda task_id: audit / f'{task_id}.json'
    failure = runtime_operation._failure(payload, RuntimePoolError(
        'RUNTIME_OPERATION_INVALID', 'Operation does not belong to this workspace and task.'))
    entries = [dict(task_id=payload['task_id'], status='WORKSPACE'),
               dict(payload, status='DISPATCHING', queue=lease['queue']),
               dict(failure, scope='task', detail=json.dumps(failure))]
    entries = json.loads(json.dumps(entries))
    audit_path = unit.instance.audit_path(payload['task_id'])
    audit_path.write_text(''.join(json.dumps(entry) + '\n' for entry in entries), encoding='utf-8')
    selected = workspace.operation_paths(payload['run_dir'], payload['task_id'])
    return SimpleNamespace(payload=payload, lease=lease, entries=entries, audit=audit_path,
                           paths=selected, failure=failure)


def refuse_dirty(unit):
    unit.instance.rpc = lambda row, discard: reply(
        row, 'BLOCKED', error_code='RUNTIME_HOST_DIRTY', error='Dirty state is unknown.',
        hosts_released=False, safe_to_resume=True, hosts=[{'host': 'blender', 'dirty': 'UNKNOWN'}])


@pytest.mark.parametrize('layout', ['flat', 'legacy'])
def test_release_settles_exact_preflight_rejection_then_preserves_dirty_runtime(root, unit, layout):
    item = rejected_pending(root, unit, layout)
    other = ready(unit.pool, 'other-workspace')
    other_before = unit.pool.validate(other)
    refuse_dirty(unit)
    result = unit.instance.release(params(item.lease))
    assert result['status'] == 'BLOCKED' and result['error_code'] == 'RUNTIME_HOST_DIRTY'
    assert result['released'] is False and unit.service.stopped == []
    assert unit.pool.validate(item.lease)['state'] == 'IDLE'
    assert unit.pool.validate(item.lease)['pending'] == {}
    assert unit.pool.validate(other) == other_before
    assert workspace._read(item.paths['result']) == item.failure
    assert workspace._read(item.paths['state'])['status'] == 'BLOCKED'
    assert task_state(read_entries(item.audit))['error_code'] == 'RUNTIME_OPERATION_INVALID'
    before = {key: item.paths[key].read_bytes() for key in ('state', 'result')}
    assert unit.instance.release(params(item.lease))['error_code'] == 'RUNTIME_HOST_DIRTY'
    assert {key: item.paths[key].read_bytes() for key in before} == before


def test_release_retries_settlement_after_result_store_without_erasing_original_error(root, unit, monkeypatch):
    from cgi_pipeline.core import runtime_operation
    item = rejected_pending(root, unit)
    refuse_dirty(unit)
    original_publish = runtime_operation._publish
    def crash_publish(*args, **kwargs):
        raise RuntimeError('Process exits after durable result, before publication.')
    monkeypatch.setattr(runtime_operation, '_publish', crash_publish)
    first = unit.instance.release(params(item.lease))
    assert first['status'] == 'NEEDS_ATTENTION'
    assert item.payload['task_id'] in unit.pool.validate(item.lease)['pending']
    result_bytes = item.paths['result'].read_bytes()
    monkeypatch.setattr(runtime_operation, '_publish', original_publish)
    second = unit.instance.release(params(item.lease))
    assert second['error_code'] == 'RUNTIME_HOST_DIRTY' and unit.pool.validate(item.lease)['pending'] == {}
    assert item.paths['result'].read_bytes() == result_bytes
    assert workspace._read(item.paths['state'])['status'] == 'BLOCKED'
    assert workspace._read(item.paths['result'])['error'] == item.failure['error']


@pytest.mark.parametrize('mismatch', ['dispatch_pid', 'dispatch_birth', 'dispatch_generation', 'dispatch_project',
    'dispatch_queue', 'terminal_pid', 'terminal_path', 'error', 'scope', 'missing_dispatch', 'running_audit',
    'unknown_audit', 'prior_attempt', 'foreign_state', 'conflicting_layout', 'malformed_audit'])
def test_release_never_settles_foreign_executed_ambiguous_or_unknown_pending(root, unit, mismatch):
    item = rejected_pending(root, unit)
    if mismatch.startswith('dispatch_'):
        field = mismatch.removeprefix('dispatch_')
        if field in ('pid', 'birth'):
            item.entries[1]['runtime']['worker'][{'pid': 'pid', 'birth': 'create_time'}[field]] += 1
        else:
            item.entries[1]['runtime'][field] = 'foreign'
    elif mismatch == 'terminal_pid':
        # Break the shared fixture reference before editing only one native snapshot.
        item.entries[-1] = json.loads(json.dumps(item.entries[-1]))
        item.entries[-1]['runtime']['worker']['pid'] += 1
    elif mismatch == 'terminal_path':
        item.entries[-1]['operation_dir'] = str(root / 'foreign')
    elif mismatch in ('error', 'scope'):
        item.entries[-1][mismatch] = 'unknown'
    elif mismatch == 'missing_dispatch':
        item.entries.pop(1)
    elif mismatch in ('running_audit', 'unknown_audit'):
        item.entries.insert(2, dict(task_id=item.payload['task_id'],
                                   status='TASK_RUNNING' if mismatch == 'running_audit' else 'NEEDS_ATTENTION'))
    elif mismatch == 'prior_attempt':
        record = workspace._read(item.paths['state'])
        record['execution_attempt'] = {'status': 'RUNNING', 'attempt_id': 'possibly-executed'}
        workspace._write(item.paths['state'], record)
    elif mismatch == 'foreign_state':
        record = workspace._read(item.paths['state'])
        record['workspace_id'] = 'foreign-workspace'
        workspace._write(item.paths['state'], record)
    elif mismatch == 'conflicting_layout':
        legacy = Path(item.payload['run_dir']) / '.info' / 'operations' / item.payload['task_id']
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / 'state.json').write_bytes(item.paths['state'].read_bytes())
    item.audit.write_text(''.join(json.dumps(entry) + '\n' for entry in item.entries), encoding='utf-8')
    if mismatch == 'malformed_audit':
        with item.audit.open('a', encoding='utf-8') as handle:
            handle.write('{truncated\n')
    before = (unit.pool.path.read_bytes(), item.paths['state'].read_bytes(), item.audit.read_bytes())
    with pytest.raises(RuntimePoolError) as failure:
        unit.instance.release(params(item.lease))
    assert failure.value.code == 'RUNTIME_BUSY'
    assert (unit.pool.path.read_bytes(), item.paths['state'].read_bytes(), item.audit.read_bytes()) == before
    assert not item.paths['result'].exists()
    assert unit.calls == [] and unit.service.stopped == []


def test_status_never_repairs_even_proven_rejected_pending(root, unit):
    item = rejected_pending(root, unit)
    before = (unit.pool.path.read_bytes(), item.paths['state'].read_bytes(), item.audit.read_bytes())
    assert unit.instance.status({})['slots'][0]['lease']['pending']
    assert (unit.pool.path.read_bytes(), item.paths['state'].read_bytes(), item.audit.read_bytes()) == before
    assert not item.paths['result'].exists() and unit.service.stopped == []


def test_live_active_owner_is_not_reconciled_from_rejected_audit(root, unit):
    item = rejected_pending(root, unit)
    unit.pool.set_busy(item.lease, item.payload['task_id'])
    before = (unit.pool.path.read_bytes(), item.paths['state'].read_bytes(), item.audit.read_bytes())
    with pytest.raises(RuntimePoolError, match='Accepted or active'):
        unit.instance.release(params(item.lease))
    assert (unit.pool.path.read_bytes(), item.paths['state'].read_bytes(), item.audit.read_bytes()) == before
    assert not item.paths['result'].exists()


@pytest.mark.parametrize('state', ['FOREIGN', 'UNKNOWN'])
def test_foreign_or_unknown_worker_process_prevents_pending_reconciliation(root, unit, state):
    item = rejected_pending(root, unit)
    unit.service.worker_state = state
    before = (unit.pool.path.read_bytes(), item.paths['state'].read_bytes(), item.audit.read_bytes())
    assert unit.instance.release(params(item.lease))['status'] == 'NEEDS_ATTENTION'
    assert (unit.pool.path.read_bytes(), item.paths['state'].read_bytes(), item.audit.read_bytes()) == before
    assert not item.paths['result'].exists() and unit.service.stopped == []


def test_rejected_pending_settlement_leaves_unproven_reservation_fenced(root, unit):
    item = rejected_pending(root, unit)
    unit.pool.reserve(item.lease, 'delivery-unknown')
    with pytest.raises(RuntimePoolError, match='Accepted or active'):
        unit.instance.release(params(item.lease))
    assert set(unit.pool.validate(item.lease)['pending']) == {'delivery-unknown'}
    assert workspace._read(item.paths['state'])['status'] == 'BLOCKED'
    assert unit.calls == [] and unit.service.stopped == []
