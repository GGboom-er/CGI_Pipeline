"""Dashboard is a shared-runtime client and a registered workflow adapter."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_import_lifespan_and_exit_do_not_manage_shared_services(tmp_path):
    probe = tmp_path / 'lifecycle_probe.py'
    trace = tmp_path / 'calls.json'
    probe.write_text('''import asyncio, atexit, json, sys
from cgi_pipeline.core import service_manager as sm
calls = []
for name in ('start_redis', 'start_worker', 'shutdown_all', 'install_exit_hooks', 'share_worker_runtime'):
    setattr(sm, name, lambda *a, _name=name, **kw: calls.append(_name))
atexit.register(lambda: open(sys.argv[1], 'w').write(json.dumps(calls)))
from cgi_pipeline.dashboard import app as dashboard
async def run():
    async with dashboard.lifespan(dashboard.app):
        pass
asyncio.run(run())
''', encoding='utf-8')
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-B', str(probe), str(trace)],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(trace.read_text()) == ['share_worker_runtime']


def _client():
    from fastapi.testclient import TestClient
    from cgi_pipeline.dashboard.app import app
    return TestClient(app)


def test_dashboard_lists_registered_workflows_and_compiles_all_legal_controls():
    client = _client()
    listed = client.get('/api/workflows').json()
    assert {wf['workflow_id'] for wf in listed['workflows']} == {
        'blender_to_maya_full_build', 'tex_to_rig_verify_and_sync'}
    cases = [('blender_to_maya_full_build', {'pack_textures': False}, 5),
             ('blender_to_maya_full_build', {'pack_textures': True}, 7)]
    cases += [('tex_to_rig_verify_and_sync', {'mode': mode, 'save_result': saved}, count)
              for mode, saved, count in [('check', False, 3), ('geometry', False, 9),
                                         ('geometry', True, 10), ('geometry_materials', False, 11),
                                         ('geometry_materials', True, 12)]]
    for workflow_id, controls, count in cases:
        result = client.post('/api/workflows/compile', json={
            'workflow_id': workflow_id, 'extra_params': controls}).json()
        assert result['status'] == 'SUCCESS', result
        assert len(result['workflow']['steps']) == count
        assert result['workflow']['effective_parameters'] == controls
        assert result['parameters']
        assert 'input_contract' in result
        assert all('step_id' in step for step in result['workflow']['steps'])


def test_dashboard_workflow_rejects_invalid_controls_without_starting_worker(monkeypatch):
    from cgi_pipeline.server import internals
    monkeypatch.setattr(internals, '_ensure_worker', lambda *a: pytest.fail('worker started'))
    client = _client()
    payload = {'workflow_id': 'tex_to_rig_verify_and_sync',
               'extra_params': {'mode': 'check', 'save_result': True}}
    compiled = client.post('/api/workflows/compile', json=payload).json()
    assert compiled['status'] == 'ERROR'
    assert 'workflow' not in compiled
    result = client.post('/api/workflows/execute', json=payload).json()
    assert result['error_code'] == 'WORKFLOW_INPUT_INVALID'
    assert result['dispatched'] is False


def test_dashboard_workflow_submission_preserves_bindings_and_formal_owner(monkeypatch):
    from cgi_pipeline.server import internals
    from unittest.mock import Mock
    submit = Mock(return_value={'status': 'SUBMITTED', 'task_id': 'wf-test'})
    monkeypatch.setattr(internals, '_submit_workflow', submit)
    payload = {'workflow_id': 'blender_to_maya_full_build', 'project': 'ysj',
               'asset_name': 'xiangC', 'source_path': 'Y:/asset.blend', 'workspace_id': 'ws',
               'business_task_id': 'PL272', 'scene_action': 'open',
               'extra_params': {'pack_textures': True, 'category': 'chr'}}
    result = _client().post('/api/workflows/execute', json=payload).json()
    assert result['dispatched'] is True
    assert submit.call_args.args[0] == payload


def test_preset_frontend_uses_compiled_workflow_routes_and_bypasses_chain_prestart():
    html = (ROOT / 'src/cgi_pipeline/dashboard/static/node_editor.html').read_text(encoding='utf-8')
    assert 'workflow_presets.js' in html
    execute = html[html.index('async function doExecute()'):]
    assert execute.index('workflowPresets.execute') < execute.index("fetch('/api/worker_status')")
    assert '/static/load_workflow' not in html


def test_preset_frontend_typed_controls_invalid_state_and_formal_submission(tmp_path):
    from cgi_pipeline.core import workflow_engine as engine
    node = shutil.which('node')
    assert node, 'This UI-state verification requires the installed Node runtime'
    plans = []
    combinations = [('blender_to_maya_full_build', {'pack_textures': value}) for value in [False, True]]
    combinations += [('tex_to_rig_verify_and_sync', {'mode': mode, 'save_result': saved})
                     for mode, saved in [('check', False), ('geometry', False), ('geometry', True),
                                         ('geometry_materials', False), ('geometry_materials', True)]]
    for workflow_id, controls in combinations:
        raw = engine.load_workflow(workflow_id)
        plan = engine.compile_workflow(raw, controls)
        plans.append({'status': 'SUCCESS', 'workflow': plan, 'parameters': raw['parameters'],
                      **engine.workflow_input_contract(plan)})
    fixture = tmp_path / 'plans.json'
    fixture.write_text(json.dumps({'list': engine.list_workflows(), 'plans': plans}), encoding='utf-8')
    result = subprocess.run([node, str(ROOT / 'tests/workflow_presets_probe.js'), str(fixture),
                             str(ROOT / 'src/cgi_pipeline/dashboard/static/workflow_presets.js')],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS:' in result.stdout
