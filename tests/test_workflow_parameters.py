"""Workflow controls compile once before workspace creation and survive dispatch."""
import json
import asyncio
import time
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.contracts import ApiContractError
from cgi_pipeline.core import workflow_engine as engine
from cgi_pipeline.server import internals
from cgi_pipeline.server.models import ExecuteWorkflowInput
from cgi_pipeline.core import tasks, dcc_factory
from cgi_pipeline.contracts import make_api_receipt
from test_workspace import root
from test_workspace_execution import runtime
from test_mcp_contract_delivery import make_server
from fastmcp import Client


def plan(workflow_id, **params):
    return engine.compile_workflow(engine.load_workflow(workflow_id), params)


@pytest.mark.parametrize("pack,count", [(False, 5), (True, 7)])
def test_build_controls_keep_packaging_before_assignment_and_save(pack, count):
    compiled = plan("blender_to_maya_full_build", pack_textures=pack)
    ids = [step["step_id"] for step in compiled["steps"]]
    assert len(ids) == count
    assert compiled["effective_parameters"] == {"pack_textures": pack}
    assert ids[-1] == ("package_asset" if pack else "save")
    assignment = next(step for step in compiled["steps"] if step["step_id"] == "apply_materials")
    if pack:
        assert ids.index("save") < ids.index("package_asset")
        archive = compiled["steps"][-1]
        assert archive["parameters"]["scene_path"] == "{{outputs.save.output_path}}"
        assert ids.index("extract_materials") < ids.index("package_textures") < ids.index("apply_materials")
        assert assignment["parameters"]["materials_path"] == "{{outputs.package_textures.output_path}}"
    else:
        assert "package_textures" not in ids
        assert assignment["parameters"]["materials_path"] == "{{outputs.extract_materials.output_path}}"


@pytest.mark.parametrize("mode,save,count", [
    ("check", False, 5), ("check", True, None),
    ("geometry", False, 9), ("geometry", True, 10),
    ("geometry_materials", False, 11), ("geometry_materials", True, 12),
])
def test_tex_controls(mode, save, count):
    if count is None:
        with pytest.raises(ApiContractError, match="check.*save_result"):
            plan("tex_to_rig_verify_and_sync", mode=mode, save_result=save)
        return
    compiled = plan("tex_to_rig_verify_and_sync", mode=mode, save_result=save)
    assert len(compiled["steps"]) == count
    assert compiled["effective_parameters"] == {"mode": mode, "save_result": save}
    assert (compiled["steps"][-1]["step_id"] == "save") == save
    if mode == "check":
        steps = {step["step_id"]: step for step in compiled["steps"]}
        assert steps["check_hierarchy_pre"]["source_path"] == "{{outputs.resolve_files.result.rig_path}}"
        assert steps["compare_pre"]["parameters"]["cache_group"] == "{{outputs.fix_hierarchy_pre.result.active_rig_root}}"
        assert not {"sync", "assign_materials", "save"} & steps.keys()


@pytest.mark.parametrize("params", [{"pack_textures": 1}, {"pack_textures": "false"},
                                    {"pack_texture": True}, {"pack_textures": None},
                                    {"run_dir": "outside"}, {"info_dir": "outside"}])
def test_strict_controls_reject_wrong_types_and_unknown_intent(params):
    with pytest.raises(ApiContractError):
        engine.compile_workflow(engine.load_workflow("blender_to_maya_full_build"), params)


def test_business_inputs_survive_and_defaults_are_discoverable():
    raw = engine.load_workflow("tex_to_rig_verify_and_sync")
    compiled = engine.compile_workflow(raw, {"category": "prp", "rig_path": "rig.ma", "source_stage": "model"})
    assert compiled["extra_params"]["category"] == "prp"
    assert compiled["extra_params"]["rig_path"] == "rig.ma"
    contract = engine.workflow_input_contract(raw)
    assert contract["input_contract"]["mode"]["default"] == "check"
    assert contract["input_contract"]["mode"]["enum"] == ["check", "geometry", "geometry_materials"]
    assert contract["mcp_call_template"]["arguments"]["params"]["extra_params"]["save_result"] is False
    assert json.loads(json.dumps(compiled)) == compiled


@pytest.mark.parametrize("target,repair", [("missing", False), ("later", False), ("__failed__", False), ("later", True)])
def test_reference_validation_recurses_into_parameters_and_repairs(target, repair):
    step = {"step_id": "first", "api_id": "pipeline.pipeline.resolve_asset_files", "parameters": {}}
    ref = {"nested": [{"path": "{{outputs." + target + ".result}}"}]}
    if repair:
        step["failure_policy"] = {"action": "repair_then_retry", "repair_step": {
            "api_id": "pipeline.pipeline.resolve_asset_files", "parameters": ref}}
    else:
        step["parameters"] = ref
    raw = {"steps": [step, {"step_id": "later", "api_id": "pipeline.pipeline.resolve_asset_files"}]}
    with pytest.raises(ValueError, match="reference"):
        engine.compile_workflow(raw, {})


def test_invalid_controls_rejected_before_binding_or_worker(root, monkeypatch):
    called = []
    monkeypatch.setattr(internals, "_bind_workspace", lambda *args, **kwargs: called.append("workspace"))
    monkeypatch.setattr(internals, "_ensure_worker", lambda *args: called.append("worker"))
    result = internals._submit_workflow({"workflow_id": "tex_to_rig_verify_and_sync",
                                         "extra_params": {"mode": "check", "save_result": True}})
    assert result["error_code"] == "WORKFLOW_INPUT_INVALID"
    assert called == [] and not (root / "projects").exists()


def test_submission_dispatches_json_safe_independent_snapshot(root, monkeypatch):
    raw = {"workflow_id": "fixture", "steps": [{"step_id": "first", "api_id": "pipeline.pipeline.resolve_asset_files"}]}
    monkeypatch.setattr(engine, "load_workflow", lambda _: raw)
    pending = []
    class Queue:
        def send_task(self, *args, **kwargs):
            pending.append(json.loads(json.dumps(kwargs["args"][0])))
    monkeypatch.setattr(internals, "_ensure_worker", lambda _: (True, "ok"))
    monkeypatch.setattr(internals, "_append_submission_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr("cgi_pipeline.core.service_manager.get_celery_app", lambda: Queue())
    result = internals._submit_workflow({"workflow_id": "fixture"})
    assert result["status"] == "SUBMITTED", result
    raw["steps"].clear()
    assert pending[0]["_workflow_plan"]["steps"][0]["step_id"] == "first"


def _install_workflow_hosts(runtime, monkeypatch):
    source = runtime.root / 'source.blend'
    source.write_bytes(b'isolated source')
    # Strict template execution requires the project config actually consumed by
    # these retained workflows; an empty config previously leaked raw templates.
    monkeypatch.setattr('cgi_pipeline.core.config_loader.load_project_config', lambda _: {
        'stages': {'tex': {'geom_roots': ['|Group|Geometry']},
                   'rig': {'geom_roots': ['|Group|Geometry', '|Cache']}}})
    def run_api(payload):
        runtime.calls.append(payload)
        api_id, params = payload['api_id'], payload['api_params']
        output = {'result': {'active_rig_root': '|Group|Geometry|RIG_geo'}}
        if api_id == 'pipeline.pipeline.resolve_asset_files':
            output = {'result': {'source_path': str(source), 'rig_path': str(runtime.source),
                                 'source_stem': 'source', 'rig_stem': 'source'}}
        elif api_id == 'blender.asset.blender_export_abc':
            output = {'output_path': params['abc_path']}
        elif api_id == 'blender.asset.blender_extract_materials' or api_id == 'pipeline.pipeline.package_textures':
            output = {'output_path': params['output_path']}
        elif api_id == 'maya.asset.maya_compare_asset_in_scene':
            output = {'compare_result': {'schema_version': 'compare_result.v1'}}
        elif api_id == 'maya.asset.save_scene':
            destination = Path(payload['run_dir']) / 'saved_result.ma'
            destination.write_bytes(b'saved scene')
            output = {'output_path': str(destination)}
        return make_api_receipt(api_id, '1.0', 'SUCCESS', time.perf_counter(), output=output)
    workers = {host: SimpleNamespace(worker_id='isolated-' + host, process=None, run_api=run_api)
               for host in ('blender', 'maya', 'pipeline')}
    monkeypatch.setattr(tasks, '_create_worker', lambda host, *args, **kwargs: workers[host])
    monkeypatch.setattr(dcc_factory, '_warm_pool', {host: {'worker': worker, 'task_count': 0}
                                                 for host, worker in workers.items()})
    return source


@pytest.mark.parametrize('workflow_id,params,count', [
    ('blender_to_maya_full_build', {'pack_textures': False}, 5),
    ('blender_to_maya_full_build', {'pack_textures': True}, 7),
    ('tex_to_rig_verify_and_sync', {'mode': 'check', 'save_result': False}, 5),
    ('tex_to_rig_verify_and_sync', {'mode': 'check', 'save_result': True}, None),
    ('tex_to_rig_verify_and_sync', {'mode': 'geometry', 'save_result': False}, 9),
    ('tex_to_rig_verify_and_sync', {'mode': 'geometry', 'save_result': True}, 10),
    ('tex_to_rig_verify_and_sync', {'mode': 'geometry_materials', 'save_result': False}, 11),
    ('tex_to_rig_verify_and_sync', {'mode': 'geometry_materials', 'save_result': True}, 12),
])
def test_public_execution_reports_parameters_and_actual_saved_results(runtime, monkeypatch, workflow_id, params, count):
    source = _install_workflow_hosts(runtime, monkeypatch)
    expected = plan(workflow_id, **params)['steps'] if count else None
    result = asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(
        workflow_id=workflow_id, source_path=str(source), extra_params=params)))
    if count is None:
        assert result['error_code'] == 'WORKFLOW_INPUT_INVALID'
        assert runtime.calls == [] and not (runtime.root / 'projects').exists()
        return
    assert result['status'] == 'WORKFLOW_SUCCESS', result
    assert [call['api_id'] for call in runtime.calls] == [step['api_id'] for step in expected]
    assert len(runtime.calls) == count
    saved = workflow_id == 'blender_to_maya_full_build' or params['save_result']
    for observed in (result, internals.get_task_result(result['task_id'])):
        assert observed['effective_parameters'] == params
        assert observed['save_requested'] is saved
        assert observed['result_saved'] is saved
        assert bool(observed['saved_results']) is saved
    report = Path(result['report_path']).read_text(encoding='utf-8')
    assert 'Effective Parameters' in report and f'| Result Saved | {saved} |' in report


def test_worker_executes_submitted_snapshot_after_definition_changes(runtime, monkeypatch):
    raw = {'workflow_id': 'fixture', 'steps': [{'step_id': 'read', 'api_id': 'maya.scene.selection.get'}]}
    directory = runtime.root / 'workflows'
    directory.mkdir()
    definition = directory / 'fixture.json'
    definition.write_text(json.dumps(raw), encoding='utf-8')
    monkeypatch.setattr(engine, '_WORKFLOWS_DIR', directory)
    submitted = internals._submit_workflow({'workflow_id': 'fixture', 'source_path': str(runtime.source)})
    raw['steps'].clear()
    definition.write_text(json.dumps(raw), encoding='utf-8')
    monkeypatch.setattr(engine, 'load_workflow', lambda _: pytest.fail('worker reread workflow file'))
    _, payload = runtime.pending.pop()
    result = tasks.execute_workflow.run(payload)
    assert result['status'] == 'WORKFLOW_SUCCESS', result
    assert len(runtime.calls) == 1 and runtime.calls[0]['api_id'] == 'maya.scene.selection.get'
    assert internals.get_task_result(submitted['task_id'])['result_saved'] is False


def test_worker_requires_snapshot_without_starting_dcc(runtime):
    result = tasks.execute_workflow.run({'task_id': 'missing-plan', 'workflow_id': 'fixture'})
    assert result['error_code'] == 'WORKFLOW_PLAN_REQUIRED'
    assert runtime.calls == []


@pytest.mark.parametrize('params', [{'mode': 'unknown'}, {'save_result': 1}, {'save_result': None},
                                   {'mode': 1}, {'run_dir': 'outside'}, {'info_dir': 'outside'}])
def test_invalid_controls_and_runtime_locations_do_not_bind_workspace(root, monkeypatch, params):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid workflow input reached workspace/worker')
    monkeypatch.setattr(internals, '_bind_workspace', forbidden)
    monkeypatch.setattr(internals, '_ensure_worker', forbidden)
    result = internals._submit_workflow({'workflow_id': 'tex_to_rig_verify_and_sync', 'extra_params': params})
    assert result['error_code'] == 'WORKFLOW_INPUT_INVALID', result


def test_disabled_producer_source_reference_and_repair_failed_scope():
    raw = {'parameters': {'enabled': {'type': 'boolean', 'default': False}}, 'steps': [
        {'step_id': 'producer', 'api_id': 'pipeline.pipeline.resolve_asset_files', 'when': {'enabled': True}},
        {'step_id': 'consumer', 'api_id': 'pipeline.pipeline.resolve_asset_files',
         'source_path': '{{outputs.producer.output_path}}'},
    ]}
    with pytest.raises(ValueError, match='reference'):
        engine.compile_workflow(raw, {})
    assert len(engine.compile_workflow(raw, {'enabled': True})['steps']) == 2
    repair = {'api_id': 'pipeline.pipeline.resolve_asset_files', 'parameters': {'result': '{{outputs.__failed__.result}}'}}
    raw['steps'] = [{'step_id': 'consumer', 'api_id': 'pipeline.pipeline.resolve_asset_files',
                     'failure_policy': {'action': 'repair_then_retry', 'repair_step': repair}}]
    assert len(engine.compile_workflow(raw, {})['steps']) == 1


def test_retained_definitions_and_fragments_list_only_two_workflows_in_isolation(root, monkeypatch):
    directory = root / 'workflows'
    directory.mkdir()
    ids = {'blender_to_maya_full_build', 'tex_to_rig_verify_and_sync'}
    for workflow_id in ids:
        shutil.copy2(engine._WORKFLOWS_DIR / (workflow_id + '.json'), directory)
    shutil.copytree(engine._WORKFLOWS_DIR / 'fragments', directory / 'fragments')
    monkeypatch.setattr(engine, '_WORKFLOWS_DIR', directory)
    assert {item['workflow_id'] for item in engine.list_workflows()} == ids
    for retired in ('blender_to_maya_packaged_build', 'tex_to_rig_verify', 'tex_to_rig_sync_geometry',
                    'blender_tex_export', 'abc_import_with_materials', 'full_cleanup_and_save'):
        with pytest.raises(FileNotFoundError):
            engine.load_workflow(retired)


@pytest.mark.parametrize('wait', [False, True])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled'])
def test_pipeline_mcp_memory_transport_preserves_workflow_result_and_recovery(runtime, monkeypatch, wait, outcome):
    from cgi_pipeline.server import tools_operations
    source = _install_workflow_hosts(runtime, monkeypatch)
    if outcome == 'failure':
        worker = tasks._create_worker('blender')
        def fail(payload):
            runtime.calls.append(payload)
            return make_api_receipt(payload['api_id'], '1.0', 'ERROR', time.perf_counter(), error='fixture failure')
        worker.run_api = fail
    async def drive(task_id, *args):
        _, payload = runtime.pending.pop(0)
        if outcome == 'cancelled':
            with (runtime.root / (task_id + '.json')).open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'task_id': task_id, 'status': 'CANCEL_REQUESTED'}) + '\n')
        tasks.execute_workflow.run(payload)
        return internals.get_task_result(task_id)
    monkeypatch.setattr(tools_operations, '_wait_for_task', drive)
    async def scenario():
        async with Client(make_server()) as client:
            returned = await client.call_tool('pipeline_execute_workflow', {'params': {
                'workflow_id': 'tex_to_rig_verify_and_sync', 'source_path': str(source), 'wait': wait,
                'extra_params': {'mode': 'geometry', 'save_result': False}}}, raise_on_error=False)
            result = returned.structured_content
            assert isinstance(result, dict), returned.content
            if not wait:
                assert result['status'] == 'SUBMITTED'
                queued = await client.call_tool('get_task_result', {'params': {'task_id': result['task_id'], 'wait_sec': 0}})
                assert queued.structured_content['effective_parameters'] == {'mode': 'geometry', 'save_result': False}
                assert queued.structured_content['result_saved'] is False
                await drive(result['task_id'])
            recovery = await client.call_tool('get_task_result', {'params': {'task_id': result['task_id']}}, raise_on_error=False)
            recovered = recovery.structured_content
            assert isinstance(recovered, dict), recovery.content
            expected = {'success': 'WORKFLOW_SUCCESS', 'failure': 'WORKFLOW_ABORTED', 'cancelled': 'CANCELLED'}[outcome]
            assert recovered['status'] == expected, recovered
            for observed in ((result, recovered) if wait else (recovered,)):
                assert observed['effective_parameters'] == {'mode': 'geometry', 'save_result': False}
                assert observed['save_requested'] is False and observed['result_saved'] is False
                assert observed['saved_results'] == []
            assert 'Effective Parameters' in Path(recovered['report_path']).read_text(encoding='utf-8')
    asyncio.run(scenario())


def test_cli_main_passes_typed_controls_to_the_same_submission_owner(root, monkeypatch, capsys):
    from cgi_pipeline import cli
    captured = []
    def submit(payload):
        captured.append(engine.compile_workflow(engine.load_workflow(payload['workflow_id']), payload['extra_params']))
        return {'task_id': 'cli-fixture', 'status': 'SUBMITTED'}
    monkeypatch.setattr(internals, '_submit_workflow', submit)
    monkeypatch.setattr('sys.argv', ['cgi', 'run-workflow', 'tex_to_rig_verify_and_sync',
                                   '--param', 'mode=geometry_materials', '--param', 'save_result=true',
                                   '--rig-path', 'rig.ma', '--category', 'prp'])
    cli.main()
    assert len(captured[0]['steps']) == 12
    assert captured[0]['effective_parameters'] == {'mode': 'geometry_materials', 'save_result': True}
    assert captured[0]['extra_params']['rig_path'] == 'rig.ma'
    assert captured[0]['extra_params']['category'] == 'prp'
    assert 'SUBMITTED' in capsys.readouterr().out


def test_queue_cancellation_preserves_parameters_in_report_and_recovery(runtime):
    submitted = internals._submit_workflow({'workflow_id': 'tex_to_rig_verify_and_sync',
                                           'extra_params': {'mode': 'geometry', 'save_result': False}})
    _, payload = runtime.pending.pop()
    with (runtime.root / (submitted['task_id'] + '.json')).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'task_id': submitted['task_id'], 'status': 'CANCEL_REQUESTED'}) + '\n')
    result = tasks.execute_workflow.run(payload)
    recovered = internals.get_task_result(submitted['task_id'])
    assert result['status'] == recovered['status'] == 'CANCELLED'
    assert recovered['effective_parameters'] == {'mode': 'geometry', 'save_result': False}
    assert recovered['result_saved'] is False and recovered['saved_results'] == []
    assert 'Effective Parameters' in Path(recovered['report_path']).read_text(encoding='utf-8')
    assert runtime.calls == []


def test_resume_retains_already_saved_output(runtime, monkeypatch):
    raw = {'workflow_id': 'save-fixture', 'steps': [{'step_id': 'save', 'api_id': 'maya.asset.save_scene'}]}
    monkeypatch.setattr(engine, 'load_workflow', lambda _: raw)
    saved = runtime.root / 'previously_saved.ma'
    saved.write_bytes(b'saved')
    submitted = internals._submit_workflow({'workflow_id': 'save-fixture', 'source_path': str(runtime.source), 'resume': True})
    monkeypatch.setattr(tasks, 'get_completed_segments', lambda _: {0})
    monkeypatch.setattr(tasks, 'restore_outputs', lambda _: {'save': {'output_path': str(saved)}})
    _, payload = runtime.pending.pop()
    result = tasks.execute_workflow.run(payload)
    recovered = internals.get_task_result(submitted['task_id'])
    assert result['status'] == 'WORKFLOW_SUCCESS', result
    assert recovered['result_saved'] is True and recovered['saved_results'] == [str(saved)]
    assert runtime.calls == []
