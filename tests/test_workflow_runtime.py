"""Admission, replay, scene isolation and delivery naming through real owners."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.core import runtime_operation, scene_session, workspace, workflow_runtime
from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError
from cgi_pipeline.core.scene_files import next_version_path
from cgi_pipeline.core.workflow_engine import compile_workflow, load_workflow, workflow_input_contract
from cgi_pipeline.core.workflow_templates import resolve_templates
from test_workspace import root, begin


def make_pool(path, memory=128):
    return RuntimePool(path, min_free_gb=8, memory_probe=lambda: memory * 1024**3,
                       allow_external_root=True)


def test_workflow_has_no_three_slot_cap_and_cannot_borrow_resident(tmp_path):
    pool = make_pool(tmp_path)
    residents = [pool.acquire('default', key) for key in ('A', 'B', 'C')]
    extra = [pool.acquire_workflow('default', 'A', f'wf-{i}') for i in range(5)]
    assert len({row['queue'] for row in residents + extra}) == 8
    assert pool.acquire('default', 'A') == residents[0]
    assert [pool.validate(row) for row in residents] == residents
    assert pool.acquire_workflow('default', 'A', 'wf-0') == extra[0]
    with pytest.raises(RuntimePoolError) as error:
        pool.acquire('default', 'D')
    assert error.value.code == 'RUNTIME_CAPACITY_BUSY'


def test_workflow_reservations_prevent_simultaneous_memory_overspend(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    pool = make_pool(tmp_path, memory=16)
    def acquire(i):
        try:
            return pool.acquire_workflow('default', 'same', f'wf-{i}')
        except RuntimePoolError as exc:
            return exc.code
    with ThreadPoolExecutor(6) as executor:
        results = list(executor.map(acquire, range(6)))
    assert sum(isinstance(item, dict) for item in results) == 2
    assert results.count('RUNTIME_MEMORY_LOW') == 4


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf'])
def test_invalid_budget_cannot_allocate(tmp_path, monkeypatch, value):
    monkeypatch.setenv('CGI_WORKFLOW_MEMORY_GB', value)
    pool = make_pool(tmp_path)
    with pytest.raises(ValueError):
        pool.acquire_workflow('default', 'A', 'wf-test')
    assert pool.status() == []


@pytest.fixture
def controller(root, monkeypatch):
    payload = begin('wf-temporary', scene_action='open')
    pool = make_pool(root / 'pool')
    sent = []
    def ensure(lease):
        pool.set_worker_info(lease, {'pid': 123, 'runtime_id': lease['runtime_id'],
                                   'generation': lease['generation'], 'root': lease['root']})
    service = SimpleNamespace(ensure=ensure, health=lambda _: {'state': 'LIVE'})
    app = SimpleNamespace(send_task=lambda *a, **kw: sent.append((a, kw)))
    monkeypatch.setattr(workflow_runtime, '_audit_path', lambda task_id: root / (task_id + '.json'))
    return payload, pool, service, app, sent


def test_low_memory_is_waiting_and_cancellable_without_launch(controller):
    payload, pool, service, app, sent = controller
    pool.memory_probe = lambda: 0
    assert not workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    assert workflow_runtime._record(payload)['phase'] == 'WAITING'
    assert pool.status() == [] and sent == []
    workflow_runtime._audit_path(payload['task_id']).write_text(json.dumps({
        'task_id': payload['task_id'], 'status': 'CANCEL_REQUESTED'}) + '\n')
    assert workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    assert workflow_runtime._record(payload)['phase'] == 'CANCELLED'
    assert sent == [] and pool.status() == []


def test_unknown_broker_acceptance_is_never_resent(controller):
    payload, pool, service, app, sent = controller
    def unknown(*a, **kw):
        sent.append(kw)
        raise TimeoutError('accepted but ack lost')
    app.send_task = unknown
    assert not workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    assert not workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    assert len(sent) == 1
    record = workflow_runtime._record(payload)
    assert record['phase'] == 'DISPATCHING'
    assert payload['task_id'] in pool.validate(record['lease'])['pending']


def test_failed_receipt_retains_scene_and_never_replays(controller):
    payload, pool, service, app, sent = controller
    assert not workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    workflow_runtime._terminal(payload, 'ERROR', 'FIXTURE', 'source failed')
    assert workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    assert workflow_runtime._record(payload)['phase'] == 'RETAINED'
    assert len(sent) == 1 and len(pool.status()) == 1


def test_success_cleanup_requires_saved_file_and_own_operation(controller, monkeypatch):
    payload, pool, service, app, sent = controller
    workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    record = workflow_runtime._record(payload)
    lease = record['lease']
    pool.unreserve(lease, payload['task_id'])
    run, state, result, _ = runtime_operation._paths(payload)
    runtime_operation._store(run, state, result, {
        **workspace.workspace_result(payload), 'task_id': payload['task_id'],
        'status': 'WORKFLOW_SUCCESS', 'save_requested': True, 'result_saved': True,
        'saved_results': [str(run / 'missing.ma')]})
    assert workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    assert workflow_runtime._record(payload)['phase'] == 'RETAINED'


def test_temporary_scene_binding_preserves_resident_binding(controller):
    payload, pool, _, _, _ = controller
    run = Path(payload['run_dir'])
    original = {'state': 'READY', 'worker_id': 'artist', 'origin': 'original.ma'}
    workspace.record_scene_binding(run, 'maya', original)
    lease = pool.acquire_workflow('default', payload['workspace_id'], payload['task_id'])
    temporary = dict(payload, runtime=lease)
    scene_session._scene_binding(temporary, 'maya', {'state': 'READY', 'worker_id': 'temporary'})
    assert workspace.get_scene_binding(run, 'maya') == original
    assert scene_session._scene_binding(temporary, 'maya')['worker_id'] == 'temporary'
    assert workspace.get_scene_binding(run, 'blender') == {}


def test_saved_success_releases_only_own_runtime_and_is_idempotent(controller, monkeypatch):
    from cgi_pipeline.core import runtime_control
    from test_runtime_control import FakeService
    payload, pool, service, app, sent = controller
    residents = [pool.acquire('default', key) for key in ('artist1', 'artist2', 'artist3')]
    workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    lease = workflow_runtime._record(payload)['lease']
    pool.unreserve(lease, payload['task_id'])
    service = FakeService()
    monkeypatch.setattr(runtime_control, 'process_state', lambda *args: 'LIVE')
    monkeypatch.setattr(runtime_control, '_publication_complete', lambda _: (True, 'complete'))
    run, state, result, _ = runtime_operation._paths(payload)
    target = run / 'ysj_chr_Test_uv_uvMaster_v001.ma'
    target.write_text('verified output')
    receipt = {**workspace.workspace_result(payload), 'task_id': payload['task_id'],
               'status': 'WORKFLOW_SUCCESS', 'save_requested': True, 'result_saved': True,
               'saved_results': [str(target)]}
    runtime_operation._store(run, state, result, receipt)
    assert workflow_runtime.attach_progress(dict(receipt))['status'] == 'PROGRESS'
    for _ in range(2):
        assert workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    assert service.stopped == [lease['runtime_id']]
    assert pool.status() == residents and target.read_text() == 'verified output'
    assert workflow_runtime.attach_progress(dict(receipt))['status'] == 'WORKFLOW_SUCCESS'
    assert len(sent) == 1


def test_dead_owner_settles_without_replay(controller):
    payload, pool, service, app, sent = controller
    workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    service.health = lambda _: {'confirmed_dead': True}
    assert workflow_runtime.advance(payload, pool=pool, service=service, app=app)
    assert workflow_runtime._record(payload)['phase'] == 'RETAINED'
    assert len(sent) == 1


def test_workflow_rejects_runtime_name_override_and_unknown_lifecycle():
    raw = load_workflow('blender_to_maya_full_build')
    with pytest.raises(ValueError, match='cannot be overridden'):
        compile_workflow(raw, {'source_name': 'override.blend'})
    with pytest.raises(ValueError, match='runtime_lifecycle'):
        compile_workflow(dict(raw, runtime_lifecycle='typo'))
    editing = load_workflow('tex_to_rig_verify_and_sync')
    assert compile_workflow(editing)['runtime_lifecycle'] == 'temporary'
    assert compile_workflow(editing, {'mode': 'geometry'})['runtime_lifecycle'] == 'resident'
    assert compile_workflow(editing, {'mode': 'geometry', 'save_result': True})['runtime_lifecycle'] == 'temporary'


def test_business_project_and_storage_remain_independent(root):
    payload = dict(task_id='wf-separated', project='ysj', workspace_project='default',
                   business_task_id='PL284', scene_action='open')
    workspace.start_operation(payload, 'workflow')
    assert Path(payload['run_dir']) == root / 'projects/default/PL284'
    assert payload['project'] == 'ysj' and payload['workspace_project'] == 'default'
    assert runtime_operation._paths(payload)[0] == Path(payload['run_dir'])
    workspace.finish_operation(payload, 'SUCCESS')
    identity = workspace.verify_workspace_identity(payload['run_dir'], 'PL284', 'default', 'PL284', 'wf-separated')
    assert identity['project'] == 'default'
    next_payload = dict(task_id='wf-other-project', project='other', workspace_project='default',
                        business_task_id='PL284', workspace_id='PL284', scene_action='open')
    workspace.start_operation(next_payload, 'workflow')
    assert next_payload['run_dir'] == payload['run_dir']
    assert next_payload['project'] == 'other'
    with pytest.raises(workspace.WorkspaceError):
        runtime_operation._paths(dict(next_payload, workspace_project='ysj'))


def test_published_case_version_and_collision_are_separate(tmp_path):
    source = tmp_path / 'server' / 'ysj_chr_yjKJ_uv_uvMaster_v001.blend'
    source.parent.mkdir()
    source.write_text('original')
    run = tmp_path / 'workspace'
    run.mkdir()
    staged, _ = scene_session._stage(str(source), run)
    assert Path(staged).name == source.name
    source.write_text('revised same source')
    second, _ = scene_session._stage(str(source), run)
    assert second != staged and Path(staged).read_text() == 'original'
    display = scene_session.source_display_name(second, run)
    assert display == source.name
    plan = compile_workflow(load_workflow('blender_to_maya_full_build'))
    contract = workflow_input_contract(plan)
    assert 'source_path' in contract['input_contract']
    assert 'source_name' not in contract['input_contract']
    save = plan['steps'][-1]['parameters']
    params = resolve_templates(save, {}, {'run_dir': str(run), 'source_name': display}, {})
    target = Path(params['save_path'])
    assert target.name == 'ysj_chr_yjKJ_uv_uvMaster_v001.ma'
    target.write_text('existing user scene')
    assert Path(next_version_path(str(target))).name == 'ysj_chr_yjKJ_uv_uvMaster_v002.ma'
    assert target.read_text() == 'existing user scene'
