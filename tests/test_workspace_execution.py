"""Public submission -> real task/report -> recovery, with an isolated fake DCC."""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.contracts import make_api_receipt
from cgi_pipeline.core import dcc_factory, tasks, workspace
from cgi_pipeline.server import internals, tools_operations
from cgi_pipeline.server.models import ExecuteApiInput, ExecuteBatchInput, ExecuteWorkflowInput
from test_workspace import root


@pytest.fixture
def runtime(root, monkeypatch):
    source = root / 'source.ma'
    source.write_text('immutable source')
    opened, calls, pending = [], [], []
    worker = SimpleNamespace(worker_id='isolated-maya', process=None, value=0)

    def run_api(payload):
        calls.append(payload)
        output = {'value': worker.value}
        if payload['api_id'].startswith('pipeline.'):
            output['source_path'] = str(source)
        return make_api_receipt(payload['api_id'], '1.0', 'SUCCESS', time.perf_counter(), output=output)

    worker.run_api = run_api
    worker.run_batch = lambda values, atomic: {
        'status': 'SUCCESS', 'results': [run_api(value) for value in values],
        'completed': len(values), 'requested': len(values), 'atomic': atomic, 'rolled_back': False,
    }
    monkeypatch.setattr(dcc_factory, '_warm_pool', {'maya': {'worker': worker, 'task_count': 0}})
    monkeypatch.setattr(tasks, '_create_worker', lambda *args, **kwargs: worker)
    def open_scene(*args):
        opened.append(args)
        worker.value = 0
        return True, ''
    monkeypatch.setattr(tasks, '_open_source_file', open_scene)
    monkeypatch.setattr(tasks, '_get_audit_path', lambda task_id: root / f'{task_id}.json')
    monkeypatch.setattr(internals, 'AUDIT_DIR', root)
    monkeypatch.setattr(internals, '_ensure_worker', lambda *args: (True, 'ready'))
    monkeypatch.setattr('cgi_pipeline.core.service_manager.start_redis', lambda: True)
    fake_app = SimpleNamespace(send_task=lambda name, args, **kwargs: pending.append((name, args[0])),
                               AsyncResult=lambda task_id: SimpleNamespace(state='PENDING'))
    monkeypatch.setattr('cgi_pipeline.core.service_manager.get_celery_app', lambda: fake_app)
    monkeypatch.setattr('cgi_pipeline.core.progress.get_latest_state', lambda task_id: {})
    for name in ('_update_progress', 'publish_event', 'persist_outputs',
                 'mark_segment_done', 'cleanup_workflow_state', 'cleanup_old_runs'):
        monkeypatch.setattr(tasks, name, lambda *args, **kwargs: None)
    monkeypatch.setattr('cgi_pipeline.core.config_loader.load_project_config', lambda project: {})

    async def wait(task_id, *args):
        name, payload = pending.pop(0)
        operation = getattr(tasks, name.rsplit('.', 1)[-1])
        result = operation.run(payload)
        recovered = internals.get_task_result(task_id)
        assert recovered.get('workspace_id') == result.get('workspace_id')
        return recovered
    monkeypatch.setattr(tools_operations, '_wait_for_task', wait)

    class MCP:
        def tool(self, **metadata):
            def decorate(function):
                setattr(self, metadata['name'], function)
                return function
            return decorate
    mcp = MCP()
    tools_operations.register_operation_tools(mcp)
    return SimpleNamespace(root=root, source=source, worker=worker, opened=opened, calls=calls,
                           pending=pending, mcp=mcp)


def execute(runtime, **values):
    if (not values.get('workspace_id') and not values.get('business_task_id')
            and not values.get('source_path')):
        values.setdefault('scene_action', 'open')
    return asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(api_id='maya.scene.selection.get', **values)))


def test_workflow_cancellation_propagates_with_partial_results_and_report(runtime, monkeypatch):
    from fastapi.testclient import TestClient
    from cgi_pipeline.dashboard import app as dashboard
    monkeypatch.setattr(dashboard, 'AUDIT_DIR', runtime.root)
    client = TestClient(dashboard.app)
    first = execute(runtime, source_path=str(runtime.source))
    original = runtime.worker.run_api
    runtime.calls.clear()

    def request_after_call(payload):
        result = original(payload)
        task_id = payload['task_id'].split('_seg')[0]
        response = client.post(f'/api/tasks/{task_id}/cancel')
        assert response.json()['requested'], response.json()
        return result

    runtime.worker.run_api = request_after_call
    monkeypatch.setattr('cgi_pipeline.core.workflow_engine.load_workflow', lambda _: {'steps': [
        {'step_id': 'first', 'api_id': 'maya.scene.selection.get', 'parameters': {}},
        {'step_id': 'second', 'api_id': 'maya.scene.selection.get', 'parameters': {}},
    ]})
    result = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(
        workflow_id='isolated-cancel', workspace_id=first['workspace_id'])))
    assert result['status'] == 'CANCELLED', result
    assert len(runtime.calls) == 1
    assert len(result['step_receipts']) == 1
    assert 'CANCELLED' in Path(result['report_path']).read_text(encoding='utf-8')
    assert json.loads(workspace.operation_paths(result['run_dir'], result['task_id'])['state'].read_text(encoding='utf-8'))['status'] == 'CANCELLED'


def test_native_batch_finishes_inflight_calls_without_false_cancellation(runtime, monkeypatch):
    from fastapi.testclient import TestClient
    from cgi_pipeline.dashboard import app as dashboard
    monkeypatch.setattr(dashboard, 'AUDIT_DIR', runtime.root)
    client = TestClient(dashboard.app)
    original = runtime.worker.run_batch

    def request_during_batch(values, atomic):
        task_id = values[0]['task_id'].rsplit('_s', 1)[0]
        assert client.post(f'/api/tasks/{task_id}/cancel').json()['requested']
        return original(values, atomic)

    runtime.worker.run_batch = request_during_batch
    result = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(calls=[
        {'api_id': 'ue.editor.python.execute', 'params': {'port': 59201, 'code': 'result = 1'}},
        {'api_id': 'ue.editor.python.execute', 'params': {'port': 59201, 'code': 'result = 2'}},
    ])))
    assert result['status'] == 'CHAIN_SUCCESS', result
    assert len(result['step_receipts']) == 2


@pytest.mark.parametrize('api_id', ['maya.scene.selection.get', 'blender.asset.blender_build_asset_info'])
@pytest.mark.parametrize('batch', [False, True])
def test_background_query_requires_target_before_directory_or_dispatch(runtime, api_id, batch):
    params = {'cache_group': 'fixture'} if api_id.startswith('blender.') else {}
    result = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(calls=[{'api_id': api_id, 'params': params}])) if batch else
                         runtime.mcp.execute_api(ExecuteApiInput(api_id=api_id, params=params)))
    assert result['error_code'] == 'SCENE_TARGET_REQUIRED'
    assert runtime.pending == [] and runtime.calls == []
    assert not (runtime.root / 'projects').exists()


@pytest.mark.parametrize('api_id,params', [
    ('maya.asset.maya_master_cleanup', {}),
    ('maya.rig.skeleton.normalize_scales', {'nodes': ['joint1']}),
    ('maya.rig.reference.update', {'operation': 'preview', 'target_map': []}),
    ('maya.animation.retarget.transfer', {'mapping': {}, 'frames': [1]}),
])
def test_background_inspect_preview_requires_target_independently_of_access(runtime, api_id, params):
    result = asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(api_id=api_id, params=params)))
    assert result.get('error_code') == 'SCENE_TARGET_REQUIRED', result
    assert runtime.pending == [] and runtime.calls == []
    assert not (runtime.root / 'projects').exists()


@pytest.mark.parametrize('batch', [False, True])
def test_apply_still_initializes_an_isolated_scene_and_mixed_batch_remains_allowed(runtime, batch):
    call = {'api_id': 'maya.asset.maya_master_cleanup', 'params': {'mode': 'fix'}}
    result = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(calls=[
        call, {'api_id': 'maya.scene.selection.get', 'params': {}},
    ]))) if batch else asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(**call)))
    assert result['status'] in ('SUCCESS', 'CHAIN_SUCCESS'), result
    assert Path(result['run_dir']).is_dir()
    assert len(runtime.opened) == 1
    assert len(runtime.calls) == (2 if batch else 1)


def test_pipeline_compare_business_file_write_allocates_workspace_without_dcc_open(runtime):
    result = asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(
        api_id='pipeline.pipeline.pipeline_compare_asset')))
    assert result['status'] == 'SUCCESS', result
    assert Path(result['run_dir']).is_dir()
    assert Path(result['report_path']).is_file()
    assert runtime.opened == []


@pytest.mark.parametrize('api_id,params', [
    ('pipeline.session.discover', {'dcc': 'ue'}),
    ('ue.editor.python.execute', {'port': 59201, 'code': 'result = 1'}),
    ('ue.editor.action.invoke', {'port': 59201, 'method': 'get_scene_info'}),
])
@pytest.mark.parametrize('business_task_id', [None, 'PL251'])
def test_default_session_calls_keep_audit_without_directories(runtime, api_id, params, business_task_id):
    before = {p for p in runtime.root.rglob('*') if p.is_dir()}
    results = [asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(api_id=api_id, params=params, business_task_id=business_task_id)))
               for _ in range(2)]
    assert all(row['status'] == 'SUCCESS' for row in results), results
    assert len(runtime.calls) == 2
    assert runtime.opened == []
    assert len({row['task_id'] for row in results}) == 2
    for row in results:
        assert not row.get('workspace_id')
        assert row.get('business_task_id') == business_task_id
        assert Path(row['audit_path']).is_file()
        assert row['receipt']['status'] == 'SUCCESS'
    assert all(call['run_dir'] == '' and call['info_dir'] == '' for call in runtime.calls)
    assert {p for p in runtime.root.rglob('*') if p.is_dir()} == before
    assert not list(runtime.root.rglob('REPORT.md'))


@pytest.mark.parametrize('native_batch', [False, True])
@pytest.mark.parametrize('business_task_id', [None, 'PL251'])
def test_default_ue_batch_has_no_workspace_and_each_receipt_survives(runtime, native_batch, business_task_id):
    if not native_batch:
        del runtime.worker.run_batch
    result = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(business_task_id=business_task_id, calls=[
        {'api_id': 'ue.editor.python.execute', 'params': {'port': 59201, 'code': 'result = 1'}},
        {'api_id': 'ue.editor.python.execute', 'params': {'port': 59201, 'code': 'result = 2'}},
    ])))
    assert result['status'] == 'CHAIN_SUCCESS', result
    assert result.get('business_task_id') == business_task_id
    assert len(result['step_receipts']) == 2
    assert len(runtime.calls) == 2
    assert runtime.opened == []
    assert not list(runtime.root.rglob('REPORT.md'))
    assert not (runtime.root / 'projects').exists()
    assert Path(result['audit_path']).is_file()


def test_audit_only_transport_failure_is_terminal_without_retry_or_directory(runtime):
    calls = []
    def fail(payload):
        calls.append(payload)
        raise RuntimeError('connection lost after dispatch')
    runtime.worker.run_api = fail
    result = asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(
        api_id='ue.editor.python.execute', params={'port': 59201, 'code': 'result = 1'})))
    assert result['status'] == 'ERROR', result
    assert len(calls) == 1
    assert 'connection lost' in result['error']
    assert not (runtime.root / 'projects').exists()
    assert 'RETRY' not in Path(result['audit_path']).read_text(encoding='utf-8')


def test_session_call_can_join_explicit_workspace(runtime):
    first = execute(runtime, source_path=str(runtime.source))
    result = asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(
        api_id='pipeline.session.discover', workspace_id=first['workspace_id'])))
    assert result['workspace_id'] == first['workspace_id']
    assert result['run_dir'] == first['run_dir']
    assert Path(result['report_path']).is_file()
    assert len(list((runtime.root / 'projects' / 'default').iterdir())) == 1


def test_public_api_batch_workflow_share_scene_and_independent_reports(runtime, monkeypatch):
    first = execute(runtime, source_path=str(runtime.source))
    wid = first['workspace_id']
    assert first['scene_session']['opened'] is True
    runtime.worker.value = 42  # Unsaved change must survive subsequent requested reads.
    second = execute(runtime, workspace_id=wid)
    assert second['receipt']['output']['value'] == 42
    assert second['scene_session']['status'] == 'REUSED'
    batch = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(workspace_id=wid, calls=[
        {'api_id': 'maya.scene.selection.get', 'params': {}},
        {'api_id': 'maya.scene.selection.get', 'params': {}},
    ])))
    assert [row['receipt']['output']['value'] for row in batch['step_receipts']] == [42, 42]
    monkeypatch.setattr('cgi_pipeline.core.workflow_engine.load_workflow', lambda workflow_id: {'steps': [
        {'step_id': 'first', 'api_id': 'maya.scene.selection.get', 'parameters': {}},
        {'api_id': 'pipeline.pipeline.resolve_asset_files', 'step_id': 'resolve_asset', 'parameters': {'category': 'chr'}},
        {'step_id': 'last', 'api_id': 'maya.scene.selection.get', 'parameters': {}},
    ]})
    workflow = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(workflow_id='isolated-test', workspace_id=wid)))
    assert workflow['status'] == 'WORKFLOW_SUCCESS', workflow
    assert len(runtime.opened) == 1
    assert runtime.worker.value == 42
    assert len(runtime.calls) == 7
    results = [first, second, batch, workflow]
    assert len({row['run_dir'] for row in results}) == 1
    assert len({row['report_path'] for row in results}) == 4
    assert all(Path(row['report_path']).is_file() for row in results)
    assert len(list((runtime.root / 'projects' / 'default').iterdir())) == 1
    manifest = workspace.operation_paths(workflow['run_dir'], workflow['task_id'])['manifest']
    assert json.loads(manifest.read_text())['wf_id'] == workflow['task_id']
    assert runtime.source.read_text() == 'immutable source'


def test_public_operations_reuse_business_task_workspace_without_manual_workspace_id(runtime, monkeypatch):
    first = execute(runtime, business_task_id='PL251', source_path=str(runtime.source))
    runtime.worker.value = 31
    second = execute(runtime, business_task_id='PL251')
    batch = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(
        business_task_id='PL251', calls=[{'api_id': 'maya.scene.selection.get'}])))
    monkeypatch.setattr('cgi_pipeline.core.workflow_engine.load_workflow', lambda workflow_id: {
        'steps': [{'step_id': 'read', 'api_id': 'maya.scene.selection.get', 'parameters': {}}],
    })
    workflow = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(
        business_task_id='PL251', workflow_id='isolated-test')))
    results = [first, second, batch, workflow]
    assert all('workspace_id' in row for row in results), results
    assert {row['workspace_id'] for row in results} == {'PL251'}
    assert {row['business_task_id'] for row in results} == {'PL251'}
    assert len({row['run_dir'] for row in results}) == 1
    assert Path(first['run_dir']).name == 'PL251'
    assert len({row['task_id'] for row in results}) == 4
    assert len({row['report_path'] for row in results}) == 4
    assert all(Path(row['report_path']).is_file() for row in results)


@pytest.mark.parametrize('asset_fields,expected', [
    ({'asset_name': 'dtb'}, 'dtb'),
    ({}, 'dcc_sdk'),
    ({'asset_name': 'untitled'}, 'dcc_sdk'),
])
def test_reused_workspace_preserves_operation_asset_name_for_workflow_resolver(runtime, monkeypatch, asset_fields, expected):
    first = execute(runtime, business_task_id='PL270', asset_name='dcc_sdk',
                    source_path=str(runtime.source))
    monkeypatch.setattr('cgi_pipeline.core.workflow_engine.load_workflow', lambda _: {
        'workflow_id': 'asset-name-regression',
        'source_resolution': {'category': 'chr', 'pipeline': 'tex'},
        'steps': [{'step_id': 'inspect', 'api_id': 'maya.scene.selection.get', 'parameters': {}}],
    })
    submitted = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(
        workflow_id='asset-name-regression', business_task_id='PL270', wait=False, **asset_fields)))
    assert submitted['status'] == 'SUBMITTED', submitted
    _, queued = runtime.pending[-1]
    assert queued['asset_name'] == expected
    assert queued['workspace_id'] == first['workspace_id']
    observed = []

    class Resolver:
        def __init__(self, _config):
            pass

        def resolve_by_stage(self, category, asset_name, **_kwargs):
            observed.append((category, asset_name))
            return {'path': str(runtime.source)}

    path, _ = tasks._resolve_workflow_source_candidate(
        queued['_workflow_plan'], {}, queued['asset_name'], queued['extra_params'], resolver_cls=Resolver)
    assert path == str(runtime.source)
    assert observed == [('chr', expected)]
    state = json.loads((Path(first['run_dir']) / '.info' / 'run_state.json').read_text())
    assert state['asset_name'] == 'dcc_sdk'


def test_new_business_task_still_requires_an_explicit_scene_target(runtime):
    result = execute(runtime, business_task_id='PL999')
    assert result['error_code'] == 'SCENE_TARGET_REQUIRED'
    assert runtime.pending == [] and runtime.calls == []
    assert not (runtime.root / 'projects').exists()


def test_progress_query_recovers_workspace_before_dcc_execution(runtime, monkeypatch):
    submitted = execute(runtime, wait=False)
    monkeypatch.setattr('cgi_pipeline.core.progress.get_latest_state', lambda task_id: {
        'event_type': 'step.start', 'message': 'running', 'progress': .5,
    })
    recovered = internals.get_task_result(submitted['task_id'])
    assert recovered['status'] == 'PROGRESS'
    assert recovered['workspace_id'] == submitted['workspace_id']
    assert recovered['report_path'] == submitted['report_path']
    assert runtime.calls == []


def test_progress_query_recovers_business_task_identity(runtime, monkeypatch):
    submitted = execute(
        runtime, business_task_id='PL251', source_path=str(runtime.source), wait=False
    )
    monkeypatch.setattr('cgi_pipeline.core.progress.get_latest_state', lambda task_id: {
        'event_type': 'step.start', 'message': 'running', 'progress': .5,
    })
    recovered = internals.get_task_result(submitted['task_id'])
    assert recovered['workspace_id'] == 'PL251'
    assert recovered['business_task_id'] == 'PL251'


@pytest.mark.parametrize('kind', ['api', 'batch', 'workflow'])
def test_lost_scene_returns_specific_failure_without_replay(runtime, monkeypatch, kind):
    first = execute(runtime)
    runtime.worker._scene_workspace_id = None
    monkeypatch.setattr('cgi_pipeline.core.workflow_engine.load_workflow', lambda workflow_id: {'steps': [
        {'step_id': 'read', 'api_id': 'maya.scene.selection.get', 'parameters': {}},
    ]})
    if kind == 'api':
        result = execute(runtime, workspace_id=first['workspace_id'])
    elif kind == 'batch':
        result = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(workspace_id=first['workspace_id'], calls=[{'api_id': 'maya.scene.selection.get'}])))
    else:
        result = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(workspace_id=first['workspace_id'], workflow_id='isolated-test')))
    assert result['status'] in ('ERROR', 'WORKFLOW_ABORTED'), result
    assert result['error_code'] == 'WORKSPACE_SCENE_UNAVAILABLE', result
    assert len(runtime.calls) == len(runtime.opened) == 1
    assert Path(result['report_path']).is_file()
    summary = Path(result['workspace_report_path']).read_text(encoding='utf-8')
    assert 'ERROR' in summary or 'ABORTED' in summary


def test_submission_failure_has_report_and_recoverable_terminal_state(runtime, monkeypatch):
    monkeypatch.setattr(internals, '_ensure_worker', lambda *args: (False, 'worker unavailable'))
    result = execute(runtime)
    assert result['status'] == 'SUBMIT_FAILED'
    assert Path(result['report_path']).is_file()
    recovered = internals.get_task_result(result['task_id'])
    assert recovered['status'] == 'SUBMIT_FAILED'
    assert recovered['workspace_id'] == result['workspace_id']
    assert runtime.calls == []


@pytest.mark.parametrize('kind', ['api', 'batch'])
def test_foreground_business_link_does_not_create_file_workspace(runtime, monkeypatch, kind):
    import threading
    from cgi_pipeline.server import foreground_client
    monkeypatch.setattr(foreground_client, 'AUDIT_DIR', runtime.root)
    runtime.worker.start = lambda: None
    monkeypatch.setattr(foreground_client, '_get_worker', lambda port: (runtime.worker, threading.RLock()))
    if kind == 'api':
        result = execute(runtime, business_task_id='PL251', execution_mode='foreground', foreground_port=7123)
    else:
        result = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(
            business_task_id='PL251', execution_mode='foreground', foreground_port=7123,
            calls=[{'api_id': 'maya.scene.selection.get'}])))
    recovered = internals.get_task_result(result['task_id'])
    assert result['business_task_id'] == recovered['business_task_id'] == 'PL251'
    assert recovered['status'] == 'SUCCESS'
    assert not result.get('workspace_id')
    assert not (runtime.root / 'projects').exists()
    assert not runtime.opened


@pytest.mark.parametrize('kind', ['api', 'batch'])
def test_explicit_foreground_workspace_keeps_native_audit_receipts_and_report(runtime, monkeypatch, kind):
    import threading
    from cgi_pipeline.server import foreground_client
    first = execute(runtime)
    monkeypatch.setattr(foreground_client, 'AUDIT_DIR', runtime.root)
    runtime.worker.start = lambda: None
    monkeypatch.setattr(foreground_client, '_get_worker', lambda port: (runtime.worker, threading.RLock()))
    runtime.worker.value = 77
    if kind == 'api':
        result = execute(runtime, workspace_id=first['workspace_id'], execution_mode='foreground', foreground_port=7123)
    else:
        result = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(workspace_id=first['workspace_id'], execution_mode='foreground', foreground_port=7123,
            calls=[{'api_id': 'maya.scene.selection.get'}, {'api_id': 'maya.scene.selection.get'}])))
    recovered = internals.get_task_result(result['task_id'])
    assert recovered['status'] == 'SUCCESS', recovered
    assert recovered['workspace_id'] == first['workspace_id']
    assert recovered['scene_session']['foreground_port'] == 7123
    assert len(list(runtime.root.rglob(f"{result['task_id']}.json"))) == 1
    receipts = [recovered['receipt']] if kind == 'api' else [row['receipt'] for row in recovered['step_receipts']]
    assert [row['output']['value'] for row in receipts] == ([77] if kind == 'api' else [77, 77])
    report = Path(result['report_path']).read_text(encoding='utf-8')
    assert 'maya.scene.selection.get' in report and '77' in report
    assert len(runtime.opened) == 1
    assert len(list((runtime.root / 'projects' / 'default').iterdir())) == 1
