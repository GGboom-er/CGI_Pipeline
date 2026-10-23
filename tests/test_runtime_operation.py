from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.core import runtime_operation, workspace
from cgi_pipeline.core.runtime_pool import RuntimePool
from cgi_pipeline.core.task_audit import read_entries, task_state
from test_workspace import root


@pytest.fixture
def operation(root, monkeypatch):
    pool = RuntimePool(root / 'pool', allow_external_root=True, memory_probe=lambda: 64 * 1024 ** 3)
    payload = {'task_id': 'operation-one', 'project': 'default', 'asset_name': 'fixture',
               'scene_action': 'open', 'api_id': 'maya.scene.selection.get'}
    workspace.start_operation(payload, 'api')
    lease = pool.acquire('default', payload['workspace_id'])
    lease = pool.set_worker_info(lease, {**{key: lease[key] for key in ('runtime_id', 'generation', 'root')},
                                       'pid': os.getpid(), 'create_time': 1.25, 'cmdline': ['worker']})
    pool.reserve(lease, payload['task_id'])
    payload['runtime'] = lease
    for key, field in [('CGI_RUNTIME_ID', 'runtime_id'), ('CGI_RUNTIME_GENERATION', 'generation'),
                       ('CGI_RUNTIME_ROOT', 'root')]:
        monkeypatch.setenv(key, lease[field])
    monkeypatch.setattr(runtime_operation, 'RuntimePool', lambda: pool)
    monkeypatch.setattr(runtime_operation, 'process_state', lambda identity, lease: 'LIVE')
    monkeypatch.setattr(runtime_operation, 'snapshot', lambda pid: dict(lease['worker']))
    audit = root / 'audit'
    audit.mkdir()
    wrap = lambda function: runtime_operation.runtime_operation_guard(
        function, audit_path=lambda task_id: audit / f'{task_id}.json')
    return SimpleNamespace(root=root, pool=pool, payload=payload, lease=lease, wrap=wrap, audit=audit)


def paths(operation):
    return workspace.operation_paths(operation.payload['run_dir'], operation.payload['task_id'])


def state(operation):
    return json.loads(paths(operation)['state'].read_text())


def success(payload):
    return {**workspace.workspace_result(payload), 'task_id': payload['task_id'], 'status': 'SUCCESS',
            'receipt': {'status': 'SUCCESS', 'output': {'unsaved_marker': [1, 2, 3]}}}


def as_legacy(payload):
    """Construct an old-layout fixture without changing production evidence."""
    selected = workspace.operation_paths(payload['run_dir'], payload['task_id'])
    legacy = Path(payload['run_dir']) / '.info' / 'operations' / payload['task_id']
    if selected['operation_dir'] == legacy:
        return
    legacy.mkdir(parents=True, exist_ok=True)
    for key, name in [('state', 'state.json'), ('result', 'result.json'),
                      ('report', 'REPORT.md'), ('manifest', 'manifest.json')]:
        if selected[key].exists():
            selected[key].replace(legacy / name)
    payload.update(operation_dir=str(legacy), report_path=str(legacy / 'REPORT.md'))
    record = workspace._read(legacy / 'state.json')
    record.update(workspace.workspace_result(payload))
    workspace._write(legacy / 'state.json', record)


def as_flat(payload):
    """Retain coverage for persisted flat operations after new calls use legacy."""
    selected = workspace.operation_paths(payload['run_dir'], payload['task_id'])
    info = Path(payload['run_dir']) / '.info'
    if selected['operation_dir'] == info:
        return
    for key, name in [('state', 'state.json'), ('result', 'result.json'),
                      ('report', 'REPORT.md'), ('manifest', 'manifest.json')]:
        if selected[key].exists():
            selected[key].replace(info / f"{payload['task_id']}_{name}")
    payload.update(operation_dir=str(info), report_path=str(info / f"{payload['task_id']}_REPORT.md"))
    record = workspace._read(info / f"{payload['task_id']}_state.json")
    record.update(workspace.workspace_result(payload))
    workspace._write(info / f"{payload['task_id']}_state.json", record)


def test_legacy_terminal_duplicate_preserves_exact_receipt_without_reexecution(operation, monkeypatch):
    as_legacy(operation.payload)
    calls = []
    execute = operation.wrap(lambda self, payload: calls.append('effect') or success(payload))
    original = execute(None, operation.payload)
    original_bytes = paths(operation)['result'].read_bytes()
    monkeypatch.setenv('CGI_RUNTIME_GENERATION', 'released-owner')
    assert execute(None, operation.payload) == original
    assert original['status'] == 'SUCCESS'
    assert paths(operation)['result'].read_bytes() == original_bytes
    assert calls == ['effect']
    assert not (Path(operation.payload['run_dir']) / '.info' / 'operation-one_result.json').exists()


def test_conflicting_layout_refuses_without_effect_or_overwriting_state(operation):
    as_flat(operation.payload)
    flat_state = paths(operation)['state']
    original_bytes = flat_state.read_bytes()
    legacy = Path(operation.payload['run_dir']) / '.info' / 'operations' / operation.payload['task_id']
    legacy.mkdir(parents=True, exist_ok=True)
    old_state = legacy / 'state.json'
    old_state.write_bytes(original_bytes)
    result = operation.wrap(lambda *args: pytest.fail('Conflicting evidence cannot execute.'))(None, operation.payload)
    assert result['error_code'] == 'WORKSPACE_OPERATION_CONFLICT'
    assert flat_state.read_bytes() == old_state.read_bytes() == original_bytes
    assert not (legacy / 'result.json').exists()
    assert not (flat_state.parent / 'operation-one_result.json').exists()


def test_flat_operation_workspace_identity_remains_checked(operation):
    as_flat(operation.payload)
    path = paths(operation)['state']
    record = workspace._read(path)
    record['workspace_id'] = 'another-workspace'
    workspace._write(path, record)
    result = operation.wrap(lambda *args: pytest.fail('Foreign workspace state cannot execute.'))(None, operation.payload)
    assert result['error_code'] == 'RUNTIME_OPERATION_INVALID'
    assert workspace._read(path)['workspace_id'] == 'another-workspace'


def test_root_cas_precedes_effect_and_terminal_receipt_is_exact(operation):
    calls = []
    def execute(self, payload):
        assert state(operation)['execution_attempt']['status'] == 'RUNNING'
        assert operation.pool.validate(operation.lease)['active_task'] == payload['task_id']
        calls.append(payload['task_id'])
        return success(payload)
    result = operation.wrap(execute)(None, operation.payload)
    assert result == success(operation.payload)
    saved = json.loads(paths(operation)['result'].read_text())
    assert saved == result
    assert state(operation)['execution_attempt']['status'] == 'TERMINAL'
    assert operation.pool.validate(operation.lease)['state'] == 'IDLE'
    assert calls == ['operation-one']


def test_terminal_duplicate_restores_before_generation_or_process_fence(operation, monkeypatch):
    calls = []
    execute = operation.wrap(lambda self, payload: calls.append('effect') or success(payload))
    original = execute(None, operation.payload)
    operation.pool.begin_release(operation.lease)
    operation.pool.finish_release(operation.lease, True)
    monkeypatch.setenv('CGI_RUNTIME_GENERATION', 'replacement')
    assert execute(None, operation.payload) == original
    assert calls == ['effect']


def test_prior_running_without_receipt_never_replays(operation):
    path = paths(operation)['state']
    value = state(operation)
    value['execution_attempt'] = {'status': 'RUNNING', 'attempt_id': 'dead-worker'}
    workspace._write(path, value)
    execute = operation.wrap(lambda *args: pytest.fail('A prior write cannot be replayed.'))
    result = execute(None, operation.payload)
    assert result['status'] == 'NEEDS_ATTENTION'
    assert result['error_code'] == 'RUNTIME_PREVIOUS_ATTEMPT_UNKNOWN'
    assert execute(None, operation.payload) == result
    assert state(operation)['status'] == 'NEEDS_ATTENTION'


def test_uncaught_exception_is_durable_unknown_outcome_and_not_retried(operation):
    calls = []
    def fail(self, payload):
        calls.append('potential write')
        raise RuntimeError('connection lost after write')
    execute = operation.wrap(fail)
    result = execute(None, operation.payload)
    assert result['status'] == 'NEEDS_ATTENTION'
    assert result['error_code'] == 'RUNTIME_EXECUTION_UNKNOWN'
    assert 'connection lost after write' in result['error']
    assert execute(None, operation.payload) == result
    assert calls == ['potential write']
    assert operation.pool.validate(operation.lease)['active_task'] == ''


@pytest.mark.parametrize('field', ['runtime_id', 'generation', 'root'])
def test_wrong_worker_environment_refuses_without_writing_foreign_operation_or_reservation(operation, monkeypatch, field):
    env = {'runtime_id': 'CGI_RUNTIME_ID', 'generation': 'CGI_RUNTIME_GENERATION', 'root': 'CGI_RUNTIME_ROOT'}
    monkeypatch.setenv(env[field], 'foreign')
    before = (paths(operation)['state'].read_bytes(), operation.pool.path.read_bytes())
    execute = operation.wrap(lambda *args: pytest.fail('Foreign worker must not execute.'))
    result = execute(None, operation.payload)
    assert result['status'] == 'BLOCKED' and result['error_code'] == 'RUNTIME_PROCESS_MISMATCH'
    assert result['workspace_id'] == operation.payload['workspace_id']
    assert (paths(operation)['state'].read_bytes(), operation.pool.path.read_bytes()) == before
    assert not paths(operation)['result'].exists()
    entries = read_entries(operation.audit / 'operation-one.json')
    assert task_state(entries)['terminal'] and entries[-1]['error_code'] == result['error_code']
    from cgi_pipeline.server import internals
    monkeypatch.setattr(internals, 'AUDIT_DIR', operation.audit)
    monkeypatch.setattr('cgi_pipeline.core.progress.get_latest_state', lambda _: {})
    monkeypatch.setattr('cgi_pipeline.core.service_manager.get_celery_app', lambda: SimpleNamespace(
        AsyncResult=lambda _: SimpleNamespace(state='PENDING')))
    recovered = internals.get_task_result('operation-one')
    assert recovered['status'] == 'BLOCKED' and recovered['terminal']
    assert recovered['error_code'] == 'RUNTIME_PROCESS_MISMATCH'
    assert set(operation.pool.validate(operation.lease)['pending']) == {operation.payload['task_id']}


def test_same_workspace_operations_take_long_execution_lock_without_metadata_deadlock(operation):
    second = {**operation.payload, 'task_id': 'operation-two'}
    workspace.start_operation(second, 'api')
    operation.pool.reserve(operation.lease, second['task_id'])
    entered, proceed = threading.Event(), threading.Event()
    effects = []
    def execute(self, payload):
        effects.append(payload['task_id'])
        # Real metadata owner can be acquired while the execution lock is held.
        with workspace._lock(Path(payload['run_dir'])):
            assert Path(payload['operation_dir']).is_dir()
        if payload['task_id'] == 'operation-one':
            entered.set()
            assert proceed.wait(timeout=5)
        return success(payload)
    guarded = operation.wrap(execute)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(guarded, None, operation.payload)
        assert entered.wait(timeout=5)
        later = executor.submit(guarded, None, second)
        assert effects == ['operation-one']
        proceed.set()
        assert first.result(timeout=5)['status'] == later.result(timeout=5)['status'] == 'SUCCESS'
    assert effects == ['operation-one', 'operation-two']


def test_subchain_inherits_busy_owner_without_second_lock_attempt_or_cache(operation):
    segment = {**operation.payload, 'task_id': 'operation-one_seg1', '_workspace_subchain': True}
    child = operation.wrap(lambda self, payload: {'status': 'SUCCESS', 'segment': 1})
    def execute(self, payload):
        assert child(None, segment) == {'status': 'SUCCESS', 'segment': 1}
        assert operation.pool.validate(operation.lease)['active_task'] == payload['task_id']
        return success(payload)
    assert operation.wrap(execute)(None, operation.payload)['status'] == 'SUCCESS'
    assert not (operation.audit / 'operation-one_seg1.json').exists()
    assert state(operation)['execution_attempt']['status'] == 'TERMINAL'


def test_legacy_without_runtime_is_an_exact_passthrough(operation):
    payload = {'task_id': 'legacy'}
    result = object()
    assert operation.wrap(lambda self, value: result)(None, payload) is result


def test_duplicate_delivery_waits_and_restores_instead_of_marking_live_owner_unknown(operation):
    entered, proceed = threading.Event(), threading.Event()
    effects = []
    def execute(self, payload):
        effects.append('write')
        entered.set()
        assert proceed.wait(timeout=5)
        return success(payload)
    guarded = operation.wrap(execute)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(guarded, None, operation.payload)
        assert entered.wait(timeout=5)
        duplicate = executor.submit(guarded, None, dict(operation.payload))
        proceed.set()
        assert duplicate.result(timeout=5) == first.result(timeout=5)
    assert effects == ['write']


def test_prior_attempt_unknown_is_preserved_after_generation_change(operation, monkeypatch):
    path = paths(operation)['state']
    value = state(operation)
    value['execution_attempt'] = {'status': 'RUNNING', 'attempt_id': 'dead-owner'}
    workspace._write(path, value)
    monkeypatch.setenv('CGI_RUNTIME_GENERATION', 'new-generation')
    result = operation.wrap(lambda *args: pytest.fail('Never replay.'))(None, operation.payload)
    assert result['status'] == 'NEEDS_ATTENTION'
    assert result['error_code'] == 'RUNTIME_PREVIOUS_ATTEMPT_UNKNOWN'
    assert state(operation)['execution_attempt']['attempt_id'] == 'dead-owner'


def test_late_result_is_readable_without_mutating_metadata_from_a_foreign_worker(operation, monkeypatch):
    path = paths(operation)['state']
    value = state(operation)
    value['execution_attempt'] = {'status': 'RUNNING', 'attempt_id': 'crashed-commit'}
    workspace._write(path, value)
    original = success(operation.payload)
    result_path = paths(operation)['result']
    workspace._write(result_path, original)
    original_bytes = result_path.read_bytes()
    monkeypatch.setenv('CGI_RUNTIME_GENERATION', 'foreign')
    assert operation.wrap(lambda *args: pytest.fail('Never replay.'))(None, operation.payload) == original
    assert result_path.read_bytes() == original_bytes
    assert state(operation)['execution_attempt']['status'] == 'RUNNING'


def test_pool_generation_mismatch_does_not_execute_or_mutate_new_owner(operation):
    operation.pool.unreserve(operation.lease, operation.payload['task_id'])
    operation.pool.begin_release(operation.lease)
    operation.pool.finish_release(operation.lease, True)
    new_lease = operation.pool.acquire('default', operation.payload['workspace_id'], rebuild=True)
    result = operation.wrap(lambda *args: pytest.fail('Wrong generation must not execute.'))(None, operation.payload)
    assert result['error_code'] == 'RUNTIME_GENERATION_MISMATCH'
    assert operation.pool.validate(new_lease)['state'] == 'STARTING'


def test_cancelled_terminal_result_is_restored_without_new_attempt(operation):
    calls = []
    def cancelled(self, payload):
        calls.append('cancellation checked by wrapped owner')
        return {**success(payload), 'status': 'CANCELLED'}
    execute = operation.wrap(cancelled)
    first = execute(None, operation.payload)
    assert execute(None, operation.payload) == first
    assert calls == ['cancellation checked by wrapped owner']


def test_wrong_subchain_environment_refuses_without_finishing_root(operation, monkeypatch):
    monkeypatch.setenv('CGI_RUNTIME_ID', 'foreign')
    segment = {**operation.payload, 'task_id': 'operation-one_seg1', '_workspace_subchain': True}
    result = operation.wrap(lambda *args: pytest.fail('Foreign segment must not execute.'))(None, segment)
    assert result['error_code'] == 'RUNTIME_PROCESS_MISMATCH'
    assert state(operation)['status'] == 'SUBMITTED'
    assert 'execution_attempt' not in state(operation)
    assert not list(operation.audit.iterdir())


def test_corrupt_result_refuses_execution_and_finishes_without_overwriting_evidence(operation):
    path = paths(operation)['result']
    path.write_text('{truncated', encoding='utf-8')
    result = operation.wrap(lambda *args: pytest.fail('Corrupt outcome cannot replay.'))(None, operation.payload)
    assert result['error_code'] == 'RUNTIME_RESULT_INVALID'
    assert path.read_text() == '{truncated'
    assert state(operation)['status'] == 'SUBMITTED'
    assert task_state(read_entries(operation.audit / 'operation-one.json'))['terminal']


@pytest.mark.parametrize('result', [{'status': 'SUCCESS'}, {'task_id': 'wrong', 'status': 'SUCCESS'},
                                  {'task_id': 'operation-one', 'status': 'RUNNING'},
                                  {'task_id': 'operation-one', 'status': 'SUCCESS', 'output': object()}])
def test_invalid_executor_return_is_cached_as_unknown_without_replay(operation, result):
    calls = []
    execute = operation.wrap(lambda *args: calls.append('effect') or result)
    first = execute(None, operation.payload)
    assert first['status'] == 'NEEDS_ATTENTION'
    assert execute(None, operation.payload) == first
    assert calls == ['effect']


def test_cached_receipt_restores_audit_if_a_different_terminal_was_recorded(operation):
    execute = operation.wrap(lambda self, payload: success(payload))
    original = execute(None, operation.payload)
    audit = operation.audit / 'operation-one.json'
    audit.write_text(json.dumps({'task_id': 'operation-one', 'scope': 'task', 'status': 'BLOCKED',
                                 'error_code': 'RUNTIME_GENERATION_MISMATCH'}) + '\n', encoding='utf-8')
    assert execute(None, operation.payload) == original
    assert task_state(read_entries(audit))['status'] == 'SUCCESS'


@pytest.mark.parametrize('layout', ['flat', 'legacy'])
def test_lookup_failure_settles_only_verified_local_operation_and_own_reservation(operation, monkeypatch, layout):
    (as_flat if layout == 'flat' else as_legacy)(operation.payload)
    operation.pool.reserve(operation.lease, 'unrelated-pending')
    def broken_lookup(*args):
        raise workspace.WorkspaceError('WORKSPACE_NOT_FOUND', 'Lookup cannot find this existing allocation.')
    monkeypatch.setattr(workspace, '_resolve', broken_lookup)
    result = operation.wrap(lambda *args: pytest.fail('A preflight failure cannot execute business code.'))(
        None, operation.payload)
    selected = paths(operation)
    assert result['status'] == 'BLOCKED' and result['error_code'] == 'WORKSPACE_NOT_FOUND'
    assert workspace._read(selected['result']) == result
    assert state(operation)['status'] == 'BLOCKED'
    assert state(operation)['execution_attempt']['result_status'] == 'BLOCKED'
    assert 'attempt_id' not in state(operation)['execution_attempt']
    assert set(operation.pool.validate(operation.lease)['pending']) == {'unrelated-pending'}
    assert task_state(read_entries(operation.audit / 'operation-one.json'))['status'] == 'BLOCKED'


@pytest.mark.parametrize('field', ['run_dir', 'operation_dir', 'report_path', 'workspace_report_path'])
def test_foreign_preflight_paths_never_write_selected_files_or_own_state(operation, field):
    before = paths(operation)['state'].read_bytes()
    foreign = operation.root / 'foreign' / field
    payload = {**operation.payload, field: str(foreign)}
    result = operation.wrap(lambda *args: pytest.fail('Foreign paths cannot execute.'))(None, payload)
    assert result['status'] == 'BLOCKED'
    assert paths(operation)['state'].read_bytes() == before
    assert not paths(operation)['result'].exists() and not foreign.exists()
    assert set(operation.pool.validate(operation.lease)['pending']) == {operation.payload['task_id']}


@pytest.mark.parametrize('ownership', ['process', 'worker_birth', 'worker_pid', 'generation', 'project'])
def test_preflight_cleanup_retains_pending_when_worker_or_lease_is_foreign(operation, monkeypatch, ownership):
    payload = dict(operation.payload)
    descriptor = {**payload['runtime'], 'worker': dict(payload['runtime']['worker'])}
    if ownership == 'process':
        monkeypatch.setattr(runtime_operation, 'process_state', lambda *args: 'FOREIGN')
    elif ownership == 'worker_birth':
        descriptor['worker']['create_time'] += 1
    elif ownership == 'worker_pid':
        descriptor['worker']['pid'] += 1
    elif ownership == 'generation':
        descriptor['generation'] = 'replacement'
    else:
        descriptor['project'] = 'foreign-project'
    payload['runtime'] = descriptor
    payload['operation_dir'] = str(operation.root / 'foreign-path')
    before = operation.pool.path.read_bytes()
    result = operation.wrap(lambda *args: pytest.fail('Rejected operation cannot execute.'))(None, payload)
    assert result['status'] == 'BLOCKED'
    assert operation.pool.path.read_bytes() == before


def test_preflight_unknown_attempt_is_conservative_and_never_replayed(operation, monkeypatch):
    selected = paths(operation)
    record = workspace._read(selected['state'])
    record['execution_attempt'] = {'status': 'RUNNING', 'attempt_id': 'unknown-delivery'}
    workspace._write(selected['state'], record)
    def broken_lookup(*args):
        raise workspace.WorkspaceError('WORKSPACE_NOT_FOUND', 'Lookup is unavailable.')
    monkeypatch.setattr(workspace, '_resolve', broken_lookup)
    result = operation.wrap(lambda *args: pytest.fail('Unknown prior effects cannot replay.'))(None, operation.payload)
    assert result['status'] == 'NEEDS_ATTENTION'
    assert result['error_code'] == 'RUNTIME_PREVIOUS_ATTEMPT_UNKNOWN'
    assert workspace._read(selected['state'])['execution_attempt']['attempt_id'] == 'unknown-delivery'
    assert workspace._read(selected['result']) == result


@pytest.mark.parametrize('preflight_failure', [False, True])
@pytest.mark.parametrize('mismatch', ['pid', 'birth', 'cmdline'])
def test_complete_foreign_worker_delivery_never_mutates_operation_or_pending(
        operation, monkeypatch, preflight_failure, mismatch):
    # Payload, lease and environment are all genuine; the receiving process is not.
    current = dict(operation.lease['worker'])
    if mismatch == 'pid':
        monkeypatch.setattr(runtime_operation.os, 'getpid', lambda: current['pid'] + 10)
    elif mismatch == 'birth':
        current['create_time'] += 1
    else:
        current['cmdline'] = ['other-worker-process']
    monkeypatch.setattr(runtime_operation, 'snapshot', lambda pid: current)
    if preflight_failure:
        def broken_lookup(*args):
            raise workspace.WorkspaceError('WORKSPACE_NOT_FOUND', 'Existing lookup failed.')
        monkeypatch.setattr(workspace, '_resolve', broken_lookup)
    before = (paths(operation)['state'].read_bytes(), operation.pool.path.read_bytes())
    result = operation.wrap(lambda *args: pytest.fail('A foreign process cannot execute.'))(None, operation.payload)
    assert result['status'] == 'BLOCKED' and result['error_code'] == 'RUNTIME_PROCESS_MISMATCH'
    assert (paths(operation)['state'].read_bytes(), operation.pool.path.read_bytes()) == before
    assert not paths(operation)['result'].exists()


@pytest.mark.parametrize('field', ['workspace_id', 'business_task_id', 'run_dir', 'operation_dir',
                                   'report_path', 'workspace_report_path'])
def test_same_id_foreign_cached_receipt_never_authorizes_restore_or_pending_cleanup(operation, field):
    selected = paths(operation)
    result = success(operation.payload)
    result[field] = ('foreign-workspace' if field.endswith('_id') else str(operation.root / 'foreign' / field))
    workspace._write(selected['result'], result)
    before = (selected['state'].read_bytes(), selected['result'].read_bytes(), operation.pool.path.read_bytes())
    response = operation.wrap(lambda *args: pytest.fail('Foreign cache cannot execute.'))(None, operation.payload)
    assert response['status'] == 'BLOCKED' and response['error_code'] == 'RUNTIME_RESULT_INVALID'
    assert (selected['state'].read_bytes(), selected['result'].read_bytes(), operation.pool.path.read_bytes()) == before
