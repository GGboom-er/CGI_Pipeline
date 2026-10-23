"""Public daily flows, isolated from brokers and DCC processes."""
import ast
import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.tools.tool import ToolResult

from cgi_pipeline.contracts import API_STATUSES, make_api_receipt
from cgi_pipeline.core import workflow_engine, task_status, workspace
from cgi_pipeline.server import internals, tools_operations
from cgi_pipeline.server.models import ExecuteApiInput
from cgi_pipeline.server.mcp_audit import McpCallAuditMiddleware
from test_mcp_public_contract import _audit_context
from test_workspace_execution import runtime, execute
from test_workspace import root


def _binding_workflow():
    return {'workflow_id': 'input-binding-fixture', 'steps': [
        {'step_id': 'build_mesh', 'api_id': 'maya.asset.maya_build_mesh_from_abc',
         'parameters': {'abc_path': '{{input.abc_path}}'}},
        {'step_id': 'assign_materials', 'api_id': 'maya.asset.maya_apply_materials',
         'parameters': {'materials_path': '{{input.materials_path}}'}},
    ]}


@pytest.mark.parametrize('status', sorted(API_STATUSES))
@pytest.mark.parametrize('host', ['maya', 'blender'])
def test_real_adapter_function_preserves_all_api_states(monkeypatch, host, status):
    source = Path(__file__).parents[1] / 'src/cgi_pipeline/hosts' / host / 'adapter.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_dispatch_api')
    outputs = []
    namespace = {'json': json, 'traceback': __import__('traceback'),
                 'cmds': object(), 'bpy': SimpleNamespace(data=SimpleNamespace(filepath='')),
                 '_write_result': lambda *args: outputs.append(args)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    monkeypatch.setattr('cgi_pipeline.execution.execute_api', lambda *a: {'status': status})
    namespace['_dispatch_api']({'task_id': 'isolated', 'api_id': 'test.api'})
    assert outputs[0][1] == status
    assert task_status.is_terminal(status)


@pytest.mark.parametrize('status', ['AUDIT_FAILED', 'PARTIAL', 'SUCCESS'])
def test_background_public_result_report_workspace_recovery_agree(runtime, status):
    receipt = make_api_receipt('maya.scene.selection.get', '1.0', status, time.perf_counter(),
                               error='quality failed' if status == 'AUDIT_FAILED' else '')
    runtime.worker.run_api = lambda payload: {'status': 'SUCCESS', 'detail': json.dumps(receipt)}
    result = execute(runtime)
    recovered = internals.get_task_result(result['task_id'])
    state = json.loads(workspace.operation_paths(result['run_dir'], result['task_id'])['state'].read_text(encoding='utf-8'))
    assert result['status'] == recovered['status'] == state['status'] == status
    assert status in Path(result['report_path']).read_text(encoding='utf-8')
    assert recovered['terminal'] is True


def test_foreground_wait_false_returns_before_host_completes_and_recovers(runtime, monkeypatch):
    from cgi_pipeline.server import foreground_client
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def host(payload, sync):
        assert sync is True
        started.set()
        assert release.wait(3)
        receipt = make_api_receipt(payload['api_id'], '1', 'PARTIAL', time.perf_counter())
        internals._append_submission_audit(payload['task_id'], payload['api_id'], 'foreground',
                                          'PARTIAL', json.dumps(receipt))
        finished.set()
        return {'task_id': payload['task_id'], 'status': 'PARTIAL', 'detail': receipt}
    monkeypatch.setattr(foreground_client, 'submit_foreground_task', host)
    try:
        result = asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(
            api_id='maya.scene.selection.get', execution_mode='foreground', foreground_port=1, wait=False)))
        assert result['status'] == 'SUBMITTED'
        assert started.wait(1) and not finished.is_set()
        assert result['task_id']
    finally:
        release.set()
    assert finished.wait(2)
    recovered = internals.get_task_result(result['task_id'])
    assert recovered['status'] == 'PARTIAL' and recovered['terminal']


def test_foreground_exception_has_recoverable_terminal(runtime):
    def broken(payload, **kwargs):
        raise RuntimeError('isolated host failure')
    payload = {'task_id': 'api-isolated-exception', 'api_id': 'test.api'}
    result = internals._run_foreground_workspace(broken, payload)
    assert result['status'] == internals.get_task_result(payload['task_id'])['status'] == 'ERROR'


def test_workflow_inputs_are_derived_and_missing_inputs_do_not_start_workers(monkeypatch):
    from unittest.mock import Mock
    workflow = _binding_workflow()
    monkeypatch.setattr(workflow_engine, 'load_workflow', lambda _: workflow)
    contract = workflow_engine.workflow_input_contract(workflow)
    assert set(contract['input_contract']) == {'abc_path', 'materials_path'}
    assert all(item['type'] == 'string' for item in contract['input_contract'].values())
    ensure = Mock(side_effect=AssertionError('must not start a worker'))
    monkeypatch.setattr(internals, '_ensure_worker', ensure)
    for values in ({}, {'abc_path': 'file.abc'}, {'abc_path': 42, 'materials_path': 'file.json'}):
        result = internals._submit_workflow({'workflow_id': 'input-binding-fixture', 'extra_params': values})
        assert result['error_code'] == 'WORKFLOW_INPUT_INVALID'
    ensure.assert_not_called()
    workflow_engine.validate_workflow_inputs(workflow, {'extra_params': {'abc_path': 'file.abc', 'materials_path': 'file.json'}})


def test_workflow_runtime_inputs_are_not_requested_from_user():
    contract = workflow_engine.workflow_input_contract(workflow_engine.load_workflow('blender_to_maya_full_build'))
    assert {name for name, row in contract['input_contract'].items() if row['required']} == {'source_path'}
    assert contract['mcp_call_template']['requires_binding'][0]['path'] == ['params', 'source_path']


def test_audit_keeps_returned_links_codes_and_timing_without_payload(tmp_path):
    middleware = McpCallAuditMiddleware(tmp_path / 'mcp.jsonl')
    async def called(context):
        return ToolResult(structured_content={
            'status': 'AUDIT_FAILED', 'task_id': 'api-one', 'workspace_id': 'work-one',
            'business_task_id': 'PL251', 'timings_ms': {'dispatch_return_ms': 12.34},
            'receipt': {'error_code': 'QUALITY_FAILED', 'error': 'secret input', 'elapsed_sec': 0.2},
            'output': {'secret': 'private asset'}, 'code': 'sensitive code'})
    asyncio.run(middleware.on_call_tool(_audit_context(), called))
    raw = (tmp_path / 'mcp.jsonl').read_text(encoding='utf-8')
    record = json.loads(raw)
    assert record['task_id'] == 'api-one' and record['business_task_id'] == 'PL251'
    assert record['error_code'] == 'QUALITY_FAILED'
    assert record['timings_ms']['api_execution_ms'] == 200
    assert 'secret input' not in raw and 'private asset' not in raw and 'sensitive code' not in raw


@pytest.mark.parametrize('status', ['SUCCESS', 'PARTIAL', 'AUDIT_FAILED'])
def test_ue_public_foreground_uses_existing_bridge_and_recovers(runtime, monkeypatch, status):
    from cgi_pipeline.hosts.ue import foreground_client
    sent = []
    def bridge(port, code, description):
        sent.append((port, code))
        # Compile the editor payload; do not import or start Unreal.
        compile(code, '<isolated-ue>', 'exec')
        return {'status': 'SUCCESS', 'result': make_api_receipt(
            'ue.scene.selection.get', '1.0', status, time.perf_counter(), output={'items': []})}
    monkeypatch.setattr(foreground_client, 'execute_python', bridge)
    result = asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(
        api_id='ue.scene.selection.get', execution_mode='foreground', foreground_port=59123)))
    assert result['status'] == status, result
    assert sent[0][0] == 59123 and "['unreal_module'] = unreal" in sent[0][1]
    assert internals.get_task_result(result['task_id'])['status'] == status


def test_ue_foreground_rejects_missing_port_before_bridge(runtime, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('must not contact UE')
    monkeypatch.setattr('cgi_pipeline.hosts.ue.foreground_client.execute_python', forbidden)
    result = asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(
        api_id='ue.scene.selection.get', execution_mode='foreground')))
    assert result['error_code'] == 'FOREGROUND_PORT_REQUIRED'


def test_every_foreground_only_ue_api_has_a_reachable_submission_and_session_binding(runtime, monkeypatch):
    from cgi_pipeline.catalog import snapshot
    from cgi_pipeline.server.tools_readonly import execute_call_template
    seen = []
    def bridge(port, code, description):
        api_id = description.removeprefix('CGI API ')
        seen.append(api_id)
        return {'status': 'SUCCESS', 'result': make_api_receipt(api_id, '1', 'SUCCESS', time.perf_counter())}
    monkeypatch.setattr('cgi_pipeline.hosts.ue.foreground_client.execute_python', bridge)
    specs, version = snapshot()
    selected = [spec for spec in specs.values() if spec['executor'] == 'ue' and spec['execution_modes'] == ['foreground']]
    assert len(selected) >= 11
    for spec in selected:
        template = execute_call_template(spec, version)
        assert any(binding['path'] == ['params', 'foreground_port'] for binding in template['requires_binding'])
        arguments = template['arguments']
        for binding in template['requires_binding']:
            kind = binding.get('type', 'string')
            value = binding.get('choices', [None])[0]
            if value is None:
                value = {'string': 'fixture', 'integer': 1, 'array': [], 'object': {},
                         'boolean': False, 'number': 1.0}[kind]
            target = arguments
            for key in binding['path'][:-1]:
                target = target[key]
            target[binding['path'][-1]] = value
        result = asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(**arguments['params'])))
        assert result['status'] == 'SUCCESS', (spec['api_id'], result)
    assert set(seen) == {spec['api_id'] for spec in selected}


def test_workflow_template_crosses_real_mcp_schema_without_execution(monkeypatch):
    from fastmcp import Client
    from test_mcp_contract_delivery import make_server
    async def run():
        async with Client(make_server()) as client:
            listing = await client.call_tool('list_workflows', {})
            row = next(item for item in listing.data['workflows'] if item['workflow_id'] == 'blender_to_maya_full_build')
            template = row['mcp_call_template']
            args = template['arguments']
            args['params']['source_path'] = 'fixture.blend'
            args['params']['extra_params'] = {'pack_textures': True}
            args['params']['wait'] = False
            monkeypatch.setattr(tools_operations, '_submit_workflow', lambda payload: {
                'status': 'SUBMITTED', 'task_id': 'wf-isolated', 'bound_inputs': payload['extra_params']})
            result = await client.call_tool(template['tool'], args)
            assert result.data['bound_inputs'] == args['params']['extra_params']
    asyncio.run(run())


def test_workflow_binds_inputs_through_task_execution_and_recovery(runtime, monkeypatch):
    from cgi_pipeline.server.models import ExecuteWorkflowInput
    monkeypatch.setattr(workflow_engine, 'load_workflow', lambda _: _binding_workflow())
    first = execute(runtime)
    runtime.calls.clear()
    abc, materials = runtime.root / 'fixture.abc', runtime.root / 'fixture_materials.json'
    abc.write_bytes(b'isolated fake abc')
    materials.write_text('{}', encoding='utf-8')
    result = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(
        workflow_id='input-binding-fixture', workspace_id=first['workspace_id'],
        extra_params={'abc_path': str(abc), 'materials_path': str(materials)})))
    assert result['status'] == 'WORKFLOW_SUCCESS', result
    assert len(runtime.calls) == 2
    assert '{{input.' not in json.dumps([call['api_params'] for call in runtime.calls])
    assert internals.get_task_result(result['task_id'])['status'] == 'WORKFLOW_SUCCESS'


def test_partial_poll_finishes_without_reporting_background_progress(monkeypatch):
    monkeypatch.setattr(tools_operations, 'get_task_result', lambda task: {'status': 'PARTIAL', 'task_id': task})
    assert asyncio.run(tools_operations._wait_for_task('api-partial', 1, 0.01))['status'] == 'PARTIAL'


def test_transport_failure_is_not_overridden_by_earlier_success_receipt():
    assert task_status.business_status({'status': 'ERROR', 'detail': {
        'api_id': 'test.api', 'status': 'SUCCESS'}}) == 'ERROR'


def test_foreground_batch_wait_false_returns_before_batch_finishes(runtime, monkeypatch):
    from cgi_pipeline.server.models import ExecuteBatchInput
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def host(payload):
        started.set()
        assert release.wait(3)
        finished.set()
        return {'task_id': payload['task_id'], 'status': 'SUCCESS'}
    monkeypatch.setattr('cgi_pipeline.server.foreground_client.submit_foreground_batch_task', host)
    try:
        result = asyncio.run(runtime.mcp.execute_batch(ExecuteBatchInput(
            calls=[{'api_id': 'maya.scene.selection.get', 'params': {}}],
            execution_mode='foreground', foreground_port=1, wait=False)))
        assert result['status'] == 'SUBMITTED' and started.wait(1) and not finished.is_set()
    finally:
        release.set()
    assert finished.wait(2)


def test_public_workflow_resolves_asset_then_reuses_workspace_source(runtime, monkeypatch):
    from cgi_pipeline.core import asset_resolver, dcc_factory
    from cgi_pipeline.server.models import ExecuteWorkflowInput
    source = runtime.root / 'tex.blend'
    source.write_bytes(b'isolated blender fixture')
    resolved = []
    class Resolver:
        def __init__(self, config):
            pass
        def resolve_by_stage(self, category, asset, **kwargs):
            resolved.append((category, asset, kwargs))
            return {'path': str(source), 'asset': asset, 'valid': True}
    monkeypatch.setattr(asset_resolver, 'AssetResolver', Resolver)
    monkeypatch.setattr('cgi_pipeline.core.config_loader.load_project_config',
                        lambda project: {'stages': {'tex': {'geom_roots': ['mesh']}}})
    original_run = runtime.worker.run_api
    def run_api(payload):
        receipt = original_run(payload)
        values = payload.get('api_params', {})
        for key in ('abc_path', 'output_path', 'save_path'):
            if values.get(key):
                receipt['output']['output_path'] = values[key]
                break
        return receipt
    runtime.worker.run_api = run_api
    maya_worker = SimpleNamespace(worker_id='daily-maya', process=None, run_api=runtime.worker.run_api)
    workers = {'blender': runtime.worker, 'maya': maya_worker}
    monkeypatch.setattr('cgi_pipeline.core.tasks._create_worker', lambda host, *args, **kwargs: workers[host])
    monkeypatch.setattr(dcc_factory, '_warm_pool', {host: {'worker': worker, 'task_count': 0}
                                                 for host, worker in workers.items()})
    result = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(
        workflow_id='blender_to_maya_full_build', project='default', asset_name='dtb')))
    assert result['status'] == 'WORKFLOW_SUCCESS', result
    assert resolved == [('chr', 'dtb', {'pipeline': 'model', 'ext_filter': ['.blend']})]
    assert len(runtime.calls) == 5
    assert '{{input.' not in json.dumps([call['api_params'] for call in runtime.calls])
    assert Path(runtime.calls[0]['source_path']).is_file()
    before_opens = len(runtime.opened)
    again = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(
        workflow_id='blender_to_maya_full_build', workspace_id=result['workspace_id'])))
    assert again['status'] == 'WORKFLOW_SUCCESS', again
    assert len(resolved) == 1 and len(runtime.opened) == before_opens
    assert internals.get_task_result(again['task_id'])['status'] == 'WORKFLOW_SUCCESS'


def test_public_source_resolving_workflow_rejects_no_source_asset_or_workspace(runtime, monkeypatch):
    from cgi_pipeline.server.models import ExecuteWorkflowInput
    def forbidden(*args, **kwargs):
        raise AssertionError('missing source must not start a worker')
    monkeypatch.setattr(internals, '_ensure_worker', forbidden)
    result = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(workflow_id='blender_to_maya_full_build')))
    assert result['error_code'] == 'WORKFLOW_INPUT_INVALID', result
    assert runtime.pending == [] and runtime.calls == []
    assert not (runtime.root / 'projects').exists()


@pytest.mark.parametrize('wait', [True, False])
def test_ue_transport_error_survives_public_result_recovery_and_audit(runtime, monkeypatch, wait):
    from cgi_pipeline.server.mcp_audit import _result_metadata
    done = threading.Event()
    original = internals._run_foreground_workspace
    def observed(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            done.set()
    monkeypatch.setattr(internals, '_run_foreground_workspace', observed)
    monkeypatch.setattr('cgi_pipeline.hosts.ue.foreground_client.execute_python', lambda *a, **k: {
        'status': 'ERROR', 'error_code': 'BRIDGE_FAILURE', 'error_type': 'ConnectionError',
        'error': 'fixture transport error'})
    result = asyncio.run(runtime.mcp.execute_api(ExecuteApiInput(api_id='ue.scene.selection.get',
        execution_mode='foreground', foreground_port=59123, wait=wait)))
    assert done.wait(2)
    recovered = internals.get_task_result(result['task_id'])
    assert recovered['status'] == 'ERROR' and recovered['terminal']
    assert recovered['error_code'] == 'BRIDGE_FAILURE'
    assert recovered['error_type'] == 'ConnectionError'
    assert recovered['error'] == 'fixture transport error'
    assert '等待 Worker' not in recovered['detail']
    assert _result_metadata(ToolResult(structured_content=recovered))['error_code'] == 'BRIDGE_FAILURE'
