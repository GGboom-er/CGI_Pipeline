import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError
from cgi_pipeline.server.runtime_dispatch import assign_runtime
from cgi_pipeline.server import internals, runtime_dispatch
from test_workspace import root
from test_runtime_control import unit, ready


def setup_workspace(tmp_path, monkeypatch, state=None):
    from cgi_pipeline.core import workspace
    run = tmp_path / 'work'
    (run / '.info').mkdir(parents=True)
    (run / '.info' / 'run_state.json').write_text(json.dumps(state or {}))
    pool = RuntimePool(tmp_path / 'pool', max_slots=3, min_free_gb=0, allow_external_root=True)
    def ensure(lease):
        pool.set_worker_info(lease, {'pid': 123, 'runtime_id': lease['runtime_id'],
                                   'generation': lease['generation'], 'root': lease['root']})
    service = SimpleNamespace(ensure=ensure)
    payload = {'project': 'default', 'workspace_id': 'test', 'run_dir': str(run),
               'task_id': 'api-test', 'api_context': {}}
    return payload, pool, service


def test_unbound_read_needs_no_runtime():
    assert assign_runtime({'task_id': 'read'}) is None


def test_assignment_is_workspace_affine(tmp_path, monkeypatch):
    payload, pool, service = setup_workspace(tmp_path, monkeypatch)
    queue = assign_runtime(payload, pool=pool, service=service)
    assert queue == payload['runtime']['queue']
    assert pool.status()[0]['pending'] == {}
    assert payload['api_context']['runtime'] == payload['runtime']
    saved = json.loads((Path(payload['run_dir']) / '.info' / 'run_state.json').read_text())
    assert saved['runtime']['generation'] == payload['runtime']['generation']


def test_legacy_scene_is_not_silently_adopted(tmp_path, monkeypatch):
    payload, pool, service = setup_workspace(tmp_path, monkeypatch,
        {'scene_hosts': {'maya': {'state': 'READY', 'worker_id': 'old'}}})
    with pytest.raises(RuntimePoolError, match='legacy') as exc:
        assign_runtime(payload, pool=pool, service=service)
    assert exc.value.code == 'RUNTIME_LEGACY_BOUND'
    assert pool.status() == []


@pytest.mark.parametrize('collection', ['api', 'batch'])
def test_pipeline_files_use_existing_queue_without_legacy_scene_or_lane(tmp_path, monkeypatch, collection):
    from cgi_pipeline.core.manifest import get_executor_queue
    payload, pool, service = setup_workspace(tmp_path, monkeypatch,
        {'scene_hosts': {'maya': {'state': 'READY', 'worker_id': 'old'}}})
    if collection == 'api':
        payload.update(api_id='pipeline.pipeline.copy_files', api_params={})
    else:
        payload['api_chain'] = [{'api_id': 'pipeline.pipeline.copy_files', 'parameters': {}}]
    path = Path(payload['run_dir']) / '.info' / 'run_state.json'
    before = path.read_bytes()
    # Full DCC capacity must not prevent a file-only operation. Using the
    # actual admission implementation exposes the original legacy-scene block.
    for key in ('A', 'B', 'C'):
        pool.acquire('default', key)
    monkeypatch.setattr(runtime_dispatch, 'assign_runtime',
                        lambda value: assign_runtime(value, pool=pool, service=service))
    available = []
    queue = runtime_dispatch.prepare_dispatch(payload, 'pipeline',
                        lambda executor: (available.append(executor) or True, 'existing worker'))
    assert queue == get_executor_queue('pipeline')
    assert available == ['pipeline']
    assert 'runtime' not in payload and 'runtime' not in payload['api_context']
    assert len(pool.status()) == 3
    assert path.read_bytes() == before


@pytest.mark.parametrize('executor', ['maya', 'blender'])
def test_scene_dispatch_keeps_legacy_continue_rejection(tmp_path, monkeypatch, executor):
    payload, pool, service = setup_workspace(tmp_path, monkeypatch,
        {'scene_hosts': {executor: {'state': 'READY', 'worker_id': 'old'}}})
    path = Path(payload['run_dir']) / '.info' / 'run_state.json'
    before = path.read_bytes()
    monkeypatch.setattr(runtime_dispatch, 'assign_runtime',
                        lambda value: assign_runtime(value, pool=pool, service=service))
    with pytest.raises(RuntimePoolError) as error:
        runtime_dispatch.prepare_dispatch(payload, executor,
                        lambda *_: pytest.fail('must not fall back to shared scene execution'))
    assert error.value.code == 'RUNTIME_LEGACY_BOUND'
    assert pool.status() == []
    assert path.read_bytes() == before


@pytest.mark.parametrize('collection', ['api', 'batch'])
def test_pipeline_file_drops_only_payload_lease_and_never_reserves_existing_scene(tmp_path, monkeypatch, collection):
    lease = {'runtime_id': 'slot-2', 'generation': 'existing-generation'}
    payload, pool, service = setup_workspace(tmp_path, monkeypatch,
        {'runtime': lease, 'scene_hosts': {'maya': {'state': 'READY', **lease}}})
    if collection == 'api':
        payload['api_id'] = 'pipeline.pipeline.copy_files'
    else:
        payload['api_chain'] = [{'api_id': 'pipeline.pipeline.copy_files', 'parameters': {}}]
    payload['runtime'] = dict(lease)
    payload['api_context'] = {'runtime': dict(lease), 'other': 'retained'}
    path = Path(payload['run_dir']) / '.info' / 'run_state.json'
    before = path.read_bytes()
    monkeypatch.setattr(runtime_dispatch, 'assign_runtime', lambda *_: pytest.fail('file API must not enter DCC admission'))
    monkeypatch.setattr(runtime_dispatch, 'RuntimePool', lambda: pytest.fail('file send must not reserve a DCC lease'))
    queue = runtime_dispatch.prepare_dispatch(payload, 'pipeline', lambda *_: (True, 'existing worker'))
    sent = []
    runtime_dispatch.send_task(SimpleNamespace(send_task=lambda *args, **kwargs: sent.append((args, kwargs))),
                               'file-task', payload, queue)
    assert sent[0][1]['args'] == [payload]
    assert 'runtime' not in payload
    assert payload['api_context'] == {'other': 'retained'}
    assert path.read_bytes() == before


def test_full_pool_rejects_before_ensure(tmp_path, monkeypatch):
    payload, pool, service = setup_workspace(tmp_path, monkeypatch)
    for key in ('A', 'B', 'C'):
        pool.acquire('default', key)
    with pytest.raises(RuntimePoolError) as exc:
        assign_runtime(payload, pool=pool, service=service)
    assert exc.value.code == 'RUNTIME_CAPACITY_BUSY'


@pytest.mark.parametrize('kind', ['api', 'batch', 'workflow'])
def test_standard_entrypoints_share_workspace_route(root, monkeypatch, kind):
    sent = []
    def assign(payload):
        assert payload['workspace_id'] and payload['operation_dir']
        payload['runtime'] = {'runtime_id': 'slot-test', 'generation': 'generation-test'}
        return 'isolated-test-queue'
    monkeypatch.setattr(runtime_dispatch, 'assign_runtime', assign)
    monkeypatch.setattr(internals, '_ensure_worker',
                        lambda *args: (True, 'admission controller') if kind == 'workflow'
                        else pytest.fail('resident calls must not use shared worker'))
    monkeypatch.setattr('cgi_pipeline.core.service_manager.start_redis', lambda: True)
    monkeypatch.setattr('cgi_pipeline.core.service_manager.get_celery_app', lambda: object())
    monkeypatch.setattr(internals, 'AUDIT_DIR', root / 'audit')
    monkeypatch.setattr('cgi_pipeline.core.config_loader.load_project_config',
                        lambda project: {'stages': {'tex': {'geom_roots': ['mesh']}}})
    monkeypatch.setattr(internals, 'send_task', lambda app, name, payload, queue:
                        sent.append((name, dict(payload), queue)))
    source = root / 'fixture.blend'
    source.write_bytes(b'route-only fixture')
    values = {'project': 'ysj', 'workspace_project': 'default', 'business_task_id': 'PL284',
              'scene_action': 'open', 'asset_name': 'fixture', 'source_path': str(source)}
    if kind == 'api':
        result = internals._submit_api_to_celery('maya.asset.exec_code',
            {**values, 'params': {'code': 'pass'}})
    elif kind == 'batch':
        result = internals._submit_chain({**values, 'batch_mode': True, 'api_chain': [
            {'api_id': 'maya.asset.exec_code', 'parameters': {'code': 'pass'}}]})
    else:
        result = internals._submit_workflow({**values, 'workflow_id': 'blender_to_maya_full_build'})
    assert result['status'] == 'SUBMITTED', result
    assert Path(result['run_dir']) == root / 'projects/default/PL284'
    assert sent[0][1]['project'] == 'ysj' and sent[0][1]['workspace_project'] == 'default'
    if kind == 'workflow':
        assert sent[0][2] == 'cgi_queue'
        assert 'runtime' not in result and 'runtime' not in sent[0][1]
        assert sent[0][1]['_workflow_plan']['runtime_lifecycle'] == 'temporary'
    else:
        assert sent[0][2] == 'isolated-test-queue'
        assert result['runtime'] == sent[0][1]['runtime']


def test_unknown_broker_acceptance_keeps_reservation(tmp_path, monkeypatch):
    payload, pool, service = setup_workspace(tmp_path, monkeypatch)
    assign_runtime(payload, pool=pool, service=service)
    monkeypatch.setattr(runtime_dispatch, 'RuntimePool', lambda: pool)
    def ambiguous_send(*args, **kwargs):
        raise TimeoutError('broker ack lost')
    with pytest.raises(RuntimePoolError) as exc:
        runtime_dispatch.send_task(SimpleNamespace(send_task=ambiguous_send), 'task', payload, payload['runtime']['queue'])
    assert exc.value.code == 'RUNTIME_SUBMISSION_UNKNOWN' and exc.value.dispatched is None
    assert payload['task_id'] in pool.validate(payload['runtime'])['pending']


def test_control_reuses_canonical_execution_without_workspace(root, monkeypatch):
    from copy import deepcopy
    from cgi_pipeline.catalog import snapshot
    from cgi_pipeline.contracts import make_api_receipt
    import time
    specs, version = snapshot()
    spec = deepcopy(specs['pipeline.session.discover'])
    spec['execution_strategy'] = 'control'
    monkeypatch.setattr(internals, 'snapshot', lambda: ({spec['api_id']: spec}, version))
    monkeypatch.setattr(internals, '_bind_workspace', lambda *a, **k: pytest.fail('control cannot bind slot'))
    monkeypatch.setattr(internals, 'AUDIT_DIR', root / 'audit')
    calls = []
    def execute(api_id, params, context, **kwargs):
        calls.append(kwargs['catalog_snapshot'])
        return make_api_receipt(api_id, spec['version'], 'SUCCESS', time.perf_counter())
    monkeypatch.setattr('cgi_pipeline.execution.execute_api', execute)
    result = internals._submit_api_to_celery(spec['api_id'], {'params': {'dcc': 'all'}})
    assert result['status'] == 'SUCCESS' and calls == [({spec['api_id']: spec}, version)]
    assert not result.get('workspace_id')
    recovered = internals.get_task_result(result['task_id'])
    assert recovered['status'] == 'SUCCESS'


def test_control_rejected_from_scene_collections_before_admission(root, monkeypatch):
    from cgi_pipeline.core.workflow_engine import validate_workflow_inputs
    from cgi_pipeline.contracts import ApiContractError
    monkeypatch.setattr(internals, '_bind_workspace', lambda *a, **k: pytest.fail('control collection must not bind'))
    result = internals._submit_chain({'api_chain': [
        {'api_id': 'pipeline.runtime.status', 'parameters': {}}]})
    assert result['error_code'] == 'BATCH_CONTRACT_ERROR'
    with pytest.raises(ApiContractError, match='standalone'):
        validate_workflow_inputs({'steps': [{'api_id': 'pipeline.runtime.status'}]}, {})


def test_dead_runtime_open_settles_previous_operation_before_reassignment(root, unit, monkeypatch):
    from cgi_pipeline.core import workspace, runtime_control
    from cgi_pipeline.core.task_audit import read_entries, task_state
    first = {'task_id': 'previous-write', 'project': 'default', 'scene_action': 'open'}
    workspace.start_operation(first, 'api')
    lease = ready(unit.pool, first['workspace_id'])
    unit.pool.reserve(lease, first['task_id'])
    unit.pool.set_busy(lease, first['task_id'])
    paths = workspace.operation_paths(first['run_dir'], first['task_id'])
    path = paths['state']
    record = workspace._read(path)
    record['execution_attempt'] = {'status': 'RUNNING'}
    workspace._write(path, record)
    audits = root / 'audit'
    audits.mkdir()
    old_audit = audits / f'{first["task_id"]}.json'
    old_audit.write_text(json.dumps({'task_id': first['task_id'], 'status': 'TASK_RUNNING', 'scope': 'task'}) + '\n')
    monkeypatch.setattr(runtime_control, '_audit_path', lambda task_id: audits / f'{task_id}.json')
    unit.service.worker_state = 'DEAD'
    unit.service.health = lambda row: {'confirmed_dead': True, 'state': 'DEAD'}
    def ensure(row):
        if row['generation'] == lease['generation']:
            raise RuntimePoolError('RUNTIME_LOST', 'dead worker')
        unit.service.worker_state = 'LIVE'
        unit.pool.set_worker_info(row, {key: row[key] for key in ('runtime_id', 'generation', 'root')})
    unit.service.ensure = ensure
    second = {'task_id': 'explicit-open', 'project': 'default', 'workspace_id': first['workspace_id'], 'scene_action': 'open'}
    workspace.start_operation(second, 'api')
    assign_runtime(second, pool=unit.pool, service=unit.service)
    assert second['runtime']['generation'] != lease['generation']
    assert workspace._read(paths['result'])['status'] == 'NEEDS_ATTENTION'
    assert task_state(read_entries(old_audit))['status'] == 'NEEDS_ATTENTION'
