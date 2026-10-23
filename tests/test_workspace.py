from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from cgi_pipeline.core import run_archive, workspace


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(run_archive, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(run_archive, 'PROJECTS_DIR', tmp_path / 'projects')
    monkeypatch.setattr(run_archive, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr('cgi_pipeline.core.service_manager.is_redis_alive', lambda: False)
    # These tests exercise workspace/report and the legacy queue boundary.
    # Slot admission has its own pool/dispatch tests; never spawn real services
    # from a unit fixture whose broker and DCC are fake.
    monkeypatch.setattr('cgi_pipeline.server.runtime_dispatch.assign_runtime', lambda payload: None)
    monkeypatch.setattr(run_archive, 'get_run_dir', lambda wid: tmp_path / 'runs' / wid)
    return tmp_path


def begin(task_id, **kwargs):
    payload = dict(task_id=task_id, project='default', asset_name='one-task', **kwargs)
    workspace.start_operation(payload, 'api')
    return payload


def as_flat(payload):
    """Seed an existing flat record in an isolated fixture; never migrate production."""
    previous = workspace.operation_paths(payload['run_dir'], payload['task_id'])
    info = Path(payload['run_dir']) / '.info'
    flat = {key: info / f"{payload['task_id']}_{name}" for key, name in
            (('state', 'state.json'), ('result', 'result.json'),
             ('report', 'REPORT.md'), ('manifest', 'manifest.json'))}
    record = workspace._read(previous['state'])
    payload.update(operation_dir=str(info), report_path=str(flat['report']))
    record.update(operation_dir=str(info), report_path=str(flat['report']))
    workspace._write(flat['state'], record)
    previous['state'].unlink()
    previous['operation_dir'].rmdir()
    return payload


def symlink_or_skip(path, target, **kwargs):
    try:
        path.symlink_to(target, **kwargs)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows account cannot create symbolic links (WinError 1314).')
        raise


def test_multiple_operations_share_workspace_and_keep_success_failure_history(root):
    first = begin('api-first')
    Path(first['report_path']).write_text('first result', encoding='utf-8')
    workspace.finish_operation(first, 'SUCCESS')
    second = begin('api-second', workspace_id=first['workspace_id'])
    Path(second['report_path']).write_text('second error', encoding='utf-8')
    workspace.finish_operation(second, 'ERROR')
    third = begin('api-third', workspace_id=first['workspace_id'])
    workspace.finish_operation(third, 'SUCCESS')
    assert first['run_dir'] == second['run_dir'] == third['run_dir']
    assert len(list((root / 'projects' / 'default').iterdir())) == 1
    assert Path(first['report_path']).read_text() == 'first result'
    assert first['report_path'] != second['report_path']
    summary = Path(first['workspace_report_path']).read_text(encoding='utf-8')
    assert all(value in summary for value in ('api-first', 'api-second', 'api-third', 'ERROR', 'SUCCESS'))
    workspace.start_operation(second, 'api')
    summary = Path(second['workspace_report_path']).read_text(encoding='utf-8')
    assert summary.count('| `api-second` |') == 1


def test_business_task_id_creates_one_stable_directory_and_reuses_without_workspace_id(root):
    first = begin('api-first', business_task_id='PL251')
    second = begin('wf-second', business_task_id='PL251')
    assert first['workspace_id'] == second['workspace_id'] == 'PL251'
    assert first['business_task_id'] == second['business_task_id'] == 'PL251'
    assert Path(first['run_dir']) == root / 'projects' / 'default' / 'PL251'
    assert first['run_dir'] == second['run_dir']
    assert first['operation_dir'] != second['operation_dir']
    assert Path(first['operation_dir']).parent == Path(second['operation_dir']).parent
    assert first['report_path'] != second['report_path']
    state = json.loads((Path(first['run_dir']) / '.info' / 'run_state.json').read_text())
    assert state['business_task_id'] == 'PL251'


def test_business_task_ids_are_isolated_and_cannot_rebind_an_existing_workspace(root):
    first = begin('api-first', business_task_id='PL251')
    other = begin('api-other', business_task_id='PL252')
    assert first['run_dir'] != other['run_dir']
    with pytest.raises(workspace.WorkspaceError, match='business task'):
        begin('api-next', workspace_id=first['workspace_id'], business_task_id='PL252')


def test_concurrent_business_task_operations_share_one_workspace(root):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        operations = list(pool.map(
            lambda i: begin(f'api-{i}', business_task_id='PL251'), range(8)
        ))
    assert {row['workspace_id'] for row in operations} == {'PL251'}
    assert len({row['run_dir'] for row in operations}) == 1
    operation_root = Path(operations[0]['run_dir']) / '.info'
    assert len(list((operation_root/'operations').glob('api-*/state.json'))) == 8
    assert not list(operation_root.glob('api-*_state.json'))


def test_flat_operation_paths_are_distinct_files_with_shared_directory(root):
    first = as_flat(begin('api-flat', business_task_id='FLAT'))
    paths = workspace.operation_paths(first['run_dir'], 'api-flat')
    assert paths['operation_dir'] == Path(first['run_dir'])/'.info'
    assert paths['report'].name == 'api-flat_REPORT.md'
    assert paths['state'].name == 'api-flat_state.json'
    assert paths['result'].name == 'api-flat_result.json'
    assert paths['manifest'].name == 'api-flat_manifest.json'
    assert not (paths['operation_dir']/'operations'/'api-flat').exists()
    for key in ('report', 'result', 'manifest'):
        paths[key].write_text(f'existing flat {key}', encoding='utf-8')
    before = {key: path.read_bytes() for key, path in paths.items() if key != 'operation_dir'}
    workspace.start_operation(first, 'api')
    assert {key: path.read_bytes() for key, path in paths.items() if key != 'operation_dir'} == before
    assert workspace.operation_paths(first['run_dir'], 'api-flat') == paths


def test_new_operation_layout_cannot_be_forced_by_payload_paths_or_flags(root):
    outside = root / 'outside'
    payload = begin('api-native', business_task_id='NATIVE', operation_layout_version=2,
                    operation_dir=str(outside), report_path=str(outside / 'REPORT.md'))
    paths = workspace.operation_paths(payload['run_dir'], payload['task_id'])
    assert paths['operation_dir'] == Path(payload['run_dir'])/'.info/operations/api-native'
    assert Path(payload['operation_dir']) == paths['operation_dir']
    assert Path(payload['report_path']) == paths['report']
    assert not outside.exists()
    assert not (Path(payload['run_dir'])/'.info/api-native_state.json').exists()


def test_legacy_receipt_stays_readable_and_new_operation_uses_compatible_layout(root):
    first = begin('api-first', business_task_id='LEGACY')
    flat = workspace.operation_paths(first['run_dir'], 'api-first')
    legacy = Path(first['run_dir'])/'.info/operations/api-old'
    legacy.mkdir(parents=True)
    record = {**workspace._read(flat['state']), 'task_id':'api-old',
              'operation_dir':str(legacy), 'report_path':str(legacy/'REPORT.md'), 'status':'SUCCESS'}
    workspace._write(legacy/'state.json', record)
    (legacy/'REPORT.md').write_text('old immutable detail', encoding='utf-8')
    before = {p.name:p.read_bytes() for p in legacy.iterdir()}
    binding = workspace.verify_workspace_identity(first['run_dir'], 'LEGACY', 'default', 'LEGACY', 'api-old')
    assert binding['allocation_receipt']=='api-old'
    second = begin('api-new', business_task_id='LEGACY')
    assert Path(second['report_path']) == Path(first['run_dir'])/'.info/operations/api-new/REPORT.md'
    assert not (Path(first['run_dir'])/'.info/api-new_state.json').exists()
    assert {p.name:p.read_bytes() for p in legacy.iterdir()}==before
    assert workspace.operation_paths(first['run_dir'], 'api-old')['state']==legacy/'state.json'


def test_two_layouts_for_same_task_are_rejected_without_fallback(root):
    first = begin('api-first')
    flat_report = Path(first['run_dir'])/'.info/api-first_REPORT.md'
    flat_report.write_text('conflicting flat detail')
    before = Path(first['workspace_report_path']).read_bytes()
    with pytest.raises(workspace.WorkspaceError) as error:
        workspace.finish_operation(first, 'SUCCESS')
    assert error.value.code=='WORKSPACE_OPERATION_CONFLICT'
    assert Path(first['workspace_report_path']).read_bytes()==before


def test_root_report_follows_latest_submission_not_latest_completion(root):
    first = begin('api-first', business_task_id='TIMELINE', submitted_at=10)
    second = begin('api-second', business_task_id='TIMELINE', submitted_at=20)
    Path(first['report_path']).write_text('old step payload', encoding='utf-8')
    Path(second['report_path']).write_text('new complete step payload\n\n[Preview](preview.png)', encoding='utf-8')
    workspace.refresh_report_for_path(second['report_path'])
    workspace.finish_operation(second, 'ERROR')
    workspace.finish_operation(first, 'SUCCESS')
    summary=Path(first['workspace_report_path']).read_text(encoding='utf-8')
    assert 'new complete step payload' in summary and 'old step payload' not in summary
    assert 'Latest operation: `api-second`' in summary and 'Status: **ERROR**' in summary
    assert '[Preview](.info/operations/api-second/preview.png)' in summary
    assert Path(first['report_path']).read_text()=='old step payload'


def test_refresh_does_not_create_workspace_for_unregistered_report(root):
    path = root/'ordinary_REPORT.md'
    path.write_text('standalone')
    before = set(root.rglob('*'))
    workspace.refresh_report_for_path(path)
    assert set(root.rglob('*'))==before


def test_metadata_link_guard_runs_without_platform_symlink_privilege(root, monkeypatch):
    first = begin('api-first')
    target=workspace.operation_paths(first['run_dir'], 'next')['state']
    original=Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path==target or original(path))
    before=set(root.rglob('*'))
    with pytest.raises(workspace.WorkspaceError, match='local'):
        begin('next', workspace_id=first['workspace_id'])
    assert set(root.rglob('*'))==before


def test_scene_bindings_merge_without_losing_other_hosts(root):
    first = begin('api-first')
    workspace.record_scene_binding(first['run_dir'], 'maya', {'worker_id': 'm'})
    workspace.record_scene_binding(first['run_dir'], 'blender', {'worker_id': 'b'})
    assert workspace.get_scene_binding(first['run_dir'], 'maya') == {'worker_id': 'm'}
    assert workspace.get_scene_binding(first['run_dir'], 'blender') == {'worker_id': 'b'}


@pytest.mark.parametrize('wid', ['../foreign', 'unknown-workspace', 'C:/production'])
def test_unknown_or_path_workspace_is_rejected_without_creation(root, wid):
    with pytest.raises(workspace.WorkspaceError):
        begin('api-next', workspace_id=wid)
    assert not (root / 'projects').exists()


def test_foreground_without_workspace_creates_nothing(root):
    assert workspace.start_operation({'task_id': 'fg-call'}, 'api', foreground=True) == {}
    assert not list(root.iterdir())


def test_project_mismatch_rejected(root):
    first = begin('api-first')
    with pytest.raises(workspace.WorkspaceError):
        workspace.start_operation(dict(task_id='api-next', project='another',
                                       workspace_id=first['workspace_id']), 'api')


def test_managed_workspace_is_not_ttl_deleted(root):
    first = begin('api-first')
    old = time.time() - 30 * 86400
    os.utime(first['run_dir'], (old, old))
    run_archive.cleanup_old_runs(max_age_days=7)
    assert Path(first['run_dir']).exists()


def test_workflow_manifests_are_per_operation(root):
    first = begin('wf-first')
    second = begin('wf-second', workspace_id=first['workspace_id'])
    for payload in (first, second):
        path = run_archive.write_manifest(payload['task_id'], 'test-workflow', 'asset', 'default',
                                          'SUCCESS', 0, {}, {'id': payload['task_id']}, [],
                                          run_dir=payload['operation_dir'])
        assert json.loads(Path(path).read_text())['wf_id'] == payload['task_id']
    assert len(list((root / 'projects' / 'default').iterdir())) == 1


def test_concurrent_first_operations_are_isolated_and_same_id_is_stable(root):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        operations = list(pool.map(lambda i: begin(f'api-{i}', submitted_at=1000), range(8)))
        repeated = list(pool.map(lambda i: begin('api-shared', submitted_at=1000), range(8)))
    assert len({row['run_dir'] for row in operations}) == 8
    assert len({row['run_dir'] for row in repeated}) == 1
    for row in operations + repeated:
        state = json.loads((Path(row['run_dir']) / '.info' / 'run_state.json').read_text())
        assert state['wf_id'] == row['workspace_id']


def test_invalid_first_project_does_not_create_workspace(root):
    with pytest.raises(workspace.WorkspaceError, match='project'):
        workspace.start_operation({'task_id': 'first', 'project': '../escape'}, 'api')
    assert list(root.iterdir()) == []


def test_operation_link_cannot_write_outside_workspace(root):
    first = begin('api-first')
    outside = root / 'outside'
    outside.mkdir()
    link = Path(first['run_dir']) / '.info' / 'operations' / 'linked'
    link.parent.mkdir(exist_ok=True)
    symlink_or_skip(link, outside, target_is_directory=True)
    with pytest.raises(workspace.WorkspaceError):
        begin('linked', workspace_id=first['workspace_id'])
    assert list(outside.iterdir()) == []


def test_summary_replaces_existing_report_atomically(root, monkeypatch):
    first = begin('api-first')
    original = Path.write_text
    report = Path(first['workspace_report_path'])
    previous = report.read_text(encoding='utf-8')
    observations = []
    def observe(path, value, *args, **kwargs):
        if path.name == 'REPORT.md.tmp':
            observations.append(report.read_text(encoding='utf-8'))
        assert path != report
        return original(path, value, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', observe)
    workspace.finish_operation(first, 'SUCCESS')
    assert observations == [previous]
    assert 'SUCCESS' in report.read_text(encoding='utf-8')


@pytest.mark.parametrize('name', ['REPORT.md', 'manifest.json', 'state.json.tmp'])
def test_operation_file_links_rejected_before_record_creation(root, name):
    first = begin('api-first')
    outside = root / 'outside.txt'
    outside.write_text('unchanged')
    operation = Path(first['run_dir']) / '.info/operations/next'
    operation.mkdir()
    symlink_or_skip(operation / name, outside)
    before = set(root.rglob('*'))
    with pytest.raises(workspace.WorkspaceError):
        begin('next', workspace_id=first['workspace_id'])
    assert set(root.rglob('*')) == before
    assert outside.read_text() == 'unchanged'


def test_root_report_link_rejected_before_new_operation_directory(root):
    first = begin('api-first')
    outside = root / 'outside.txt'
    outside.write_text('unchanged')
    symlink_or_skip(Path(first['run_dir']) / 'REPORT.md.tmp', outside)
    before = set(root.rglob('*'))
    with pytest.raises(workspace.WorkspaceError):
        begin('next', workspace_id=first['workspace_id'])
    assert set(root.rglob('*')) == before
    assert outside.read_text() == 'unchanged'


def test_manifest_writer_rejects_file_link(root):
    first = begin('api-first')
    outside = root / 'outside.txt'
    outside.write_text('unchanged')
    symlink_or_skip(workspace.operation_paths(first['run_dir'], 'api-first')['manifest'], outside)
    with pytest.raises(ValueError):
        run_archive.write_manifest('api-first', 'workflow', 'asset', 'default', 'SUCCESS', 0, {}, {}, [], run_dir=first['operation_dir'])
    assert outside.read_text() == 'unchanged'


def test_mode_policy_uses_one_catalog_snapshot_for_the_complete_call_collection(root, monkeypatch):
    from cgi_pipeline import catalog
    from unittest.mock import Mock

    read = Mock(wraps=catalog.snapshot)
    monkeypatch.setattr(catalog, 'snapshot', read)
    payload = {'task_id': 'preview-policy', 'api_chain': [
        {'api_id': 'maya.asset.maya_master_cleanup', 'parameters': {}},
        {'api_id': 'maya.rig.skeleton.normalize_scales', 'parameters': {'nodes': ['joint1']}},
    ]}
    with pytest.raises(workspace.WorkspaceError) as error:
        workspace.start_operation(payload, 'batch')
    assert error.value.code == 'SCENE_TARGET_REQUIRED'
    assert read.call_count == 1
    assert list(root.iterdir()) == []


def test_invalid_mode_selector_is_rejected_before_creating_workspace(root):
    payload = {'task_id': 'invalid-selector', 'api_id': 'maya.asset.maya_master_cleanup',
               'api_params': {'mode': 'not-a-mode'}}
    with pytest.raises(workspace.ApiContractError):
        workspace.start_operation(payload, 'api')
    assert list(root.iterdir()) == []


def test_mixed_pipeline_then_dcc_inspect_chain_retains_initialization_boundary(root):
    payload = {'task_id': 'resolve-inspect', 'api_chain': [
        {'api_id': 'pipeline.session.discover', 'parameters': {'dcc': 'maya'}},
        {'api_id': 'maya.scene.selection.get', 'parameters': {}},
    ]}
    result = workspace.start_operation(payload, 'chain')
    assert Path(result['run_dir']).is_dir()


def test_pure_memory_inspect_keeps_the_conservative_scene_target_policy(root):
    payload = {'task_id': 'memory-inspect', 'api_id': 'maya.scene.graph.validate_delta',
               'api_params': {'before': {}, 'after': {}}}
    with pytest.raises(workspace.WorkspaceError) as error:
        workspace.start_operation(payload, 'api')
    assert error.value.code == 'SCENE_TARGET_REQUIRED'
    assert list(root.iterdir()) == []


def test_identity_verification_is_read_only_and_ignores_mutable_runtime_state(root, monkeypatch):
    first = begin('api-first', business_task_id='PL266')
    workspace.finish_operation(first, 'SUCCESS')
    state_path = Path(first['run_dir']) / '.info' / 'run_state.json'
    state = json.loads(state_path.read_text())
    state['scene_hosts'] = {'maya': {'state': 'READY', 'worker_id': 'legacy'}}
    state_path.write_text(json.dumps(state))
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(run_archive, 'get_run_dir', lambda *_: pytest.fail('Redis lookup in pure verification'))
    identity = workspace.verify_workspace_identity(first['run_dir'], 'PL266', 'default', 'PL266', 'api-first')
    assert identity == dict(workspace_root=str(Path(first['run_dir']).resolve()),
                            workspace_id='PL266', project='default', business_task_id='PL266',
                            run_state='.info/run_state.json', allocation_receipt='api-first')
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('field,value', [('workspace_id', 'PL267'), ('project', 'another'),
                                        ('business_task_id', 'PL267')])
def test_identity_rejects_mismatched_fields_without_writing(root, field, value):
    first = begin('api-first', business_task_id='PL266')
    args = dict(workspace_root=first['run_dir'], workspace_id='PL266',
                project='default', business_task_id='PL266')
    args[field] = value
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(workspace.WorkspaceError):
        workspace.verify_workspace_identity(**args)
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('status', ['SUBMITTED', 'PARTIAL', 'BLOCKED', 'ERROR'])
def test_identity_rejects_unconfirmed_allocation_receipt(root, status):
    first = begin('api-first', business_task_id='PL266')
    workspace.finish_operation(first, status)
    with pytest.raises(workspace.WorkspaceError) as error:
        workspace.verify_workspace_identity(first['run_dir'], 'PL266', 'default', 'PL266', 'api-first')
    assert error.value.code == 'WORKSPACE_RECEIPT_INVALID'


@pytest.mark.parametrize('status', ['SUCCESS', 'CHAIN_SUCCESS', 'WORKFLOW_SUCCESS',
                                   'SUCCESS_WITH_WARNINGS', 'WORKFLOW_SUCCESS_WITH_WARNINGS'])
def test_identity_accepts_canonical_native_success_receipts_without_rewriting(root, status):
    first = begin('operation-first', business_task_id='PL266')
    workspace.finish_operation(first, status)
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    identity = workspace.verify_workspace_identity(first['run_dir'], 'PL266', 'default', 'PL266', 'operation-first')
    assert identity['allocation_receipt'] == 'operation-first'
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_identity_rejects_missing_receipt_and_foreign_receipt_metadata(root):
    first = begin('api-first', business_task_id='PL266')
    workspace.finish_operation(first, 'SUCCESS')
    with pytest.raises(workspace.WorkspaceError):
        workspace.verify_workspace_identity(first['run_dir'], 'PL266', 'default', 'PL266', 'api-missing')
    receipt = workspace.operation_paths(first['run_dir'], 'api-first')['state']
    state = json.loads(receipt.read_text())
    state['business_task_id'] = 'PL267'
    receipt.write_text(json.dumps(state))
    with pytest.raises(workspace.WorkspaceError):
        workspace.verify_workspace_identity(first['run_dir'], 'PL266', 'default', 'PL266', 'api-first')


def test_initial_state_write_is_atomic_and_failure_is_not_swallowed(root, monkeypatch):
    original = Path.write_text
    def fail_state(path, value, *args, **kwargs):
        if path.name.startswith('run_state.json.'):
            raise OSError('injected first state failure')
        return original(path, value, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail_state)
    with pytest.raises((OSError, workspace.WorkspaceError), match='injected first state failure'):
        run_archive.create_run_dir('PL266', 'default', 'task', directory_name='PL266', business_task_id='PL266')
    assert not list(root.rglob('run_state.json'))


def test_bad_cache_path_does_not_create_or_modify_foreign_directory(root, monkeypatch):
    import sys
    from types import SimpleNamespace
    foreign = root / 'foreign'
    redis = SimpleNamespace(hget=lambda *_: str(foreign))
    monkeypatch.setitem(sys.modules, 'redis', SimpleNamespace(Redis=lambda **_: redis))
    monkeypatch.setattr('cgi_pipeline.core.service_manager.is_redis_alive', lambda: True)
    with pytest.raises(workspace.WorkspaceError):
        run_archive.create_run_dir('PL266', 'default', 'task', business_task_id='PL266')
    assert not foreign.exists()


def test_cross_project_cache_is_rejected_before_any_write(root, monkeypatch):
    import sys
    from types import SimpleNamespace
    first = begin('api-first', business_task_id='PL266')
    redis = SimpleNamespace(hget=lambda *_: first['run_dir'])
    monkeypatch.setitem(sys.modules, 'redis', SimpleNamespace(Redis=lambda **_: redis))
    monkeypatch.setattr('cgi_pipeline.core.service_manager.is_redis_alive', lambda: True)
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(workspace.WorkspaceError):
        run_archive.create_run_dir('PL266', 'another', 'task', business_task_id='PL266')
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_filesystem_recovery_rejects_matching_id_with_wrong_project(root):
    first = begin('api-first', business_task_id='PL266')
    path = Path(first['run_dir']) / '.info' / 'run_state.json'
    state = json.loads(path.read_text())
    state['project'] = 'another'
    path.write_text(json.dumps(state))
    with pytest.raises(workspace.WorkspaceError):
        run_archive.create_run_dir('PL266', 'default', 'task', business_task_id='PL266')


def test_identity_rejects_outside_root_without_creating_it(root):
    outside = root / 'foreign'
    with pytest.raises(workspace.WorkspaceError) as error:
        workspace.verify_workspace_identity(outside, 'PL266', 'default', 'PL266')
    assert error.value.code == 'WORKSPACE_PATH_INVALID'
    assert not outside.exists()


def test_identity_rejects_corrupt_state_without_repair(root):
    first = begin('api-first', business_task_id='PL266')
    path = Path(first['run_dir']) / '.info' / 'run_state.json'
    path.write_text('{bad json')
    with pytest.raises(workspace.WorkspaceError) as error:
        workspace.verify_workspace_identity(first['run_dir'], 'PL266', 'default', 'PL266')
    assert error.value.code == 'WORKSPACE_STATE_INVALID'
    assert path.read_text() == '{bad json'


def test_identity_cold_import_and_verification_have_no_service_or_write_side_effects(root):
    import subprocess
    import sys
    first = begin('api-first', business_task_id='PL266')
    workspace.finish_operation(first, 'SUCCESS')
    script = '''
import json, socket, subprocess, sys
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError('service/write side effect')
subprocess.Popen.__init__ = forbidden
socket.socket.connect = socket.socket.bind = socket.socket.listen = forbidden
Path.mkdir = Path.write_text = forbidden
from cgi_pipeline.core.workspace import verify_workspace_identity
from cgi_pipeline.core import run_archive
run_archive.PROJECT_ROOT = Path(sys.argv[2])
identity = verify_workspace_identity(sys.argv[1], 'PL266', 'default', 'PL266', 'api-first')
assert not any(name in sys.modules for name in ('maya', 'bpy', 'unreal', 'redis', 'cgi_pipeline.core.service_manager'))
print(json.dumps(identity))
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, first['run_dir'], str(root)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['workspace_id'] == 'PL266'
