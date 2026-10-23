"""Current scene-source reads reuse verified relocation paths, never edit history."""
from pathlib import Path

import pytest

from cgi_pipeline.catalog import snapshot
from cgi_pipeline.core import workspace, workspace_relocation as move
from cgi_pipeline.core.workflow_engine import _workflow_source_from_existing_owner
from test_workspace_relocation import add_old_operation, relocation_case, root, seal


@pytest.mark.parametrize('dcc,suffix', [('maya', '.ma'), ('blender', '.blend')])
def test_recorded_source_follows_real_relocation_without_rewriting_raw_binding(relocation_case, dcc, suffix):
    c = relocation_case
    source = c.source / ('original' + suffix)
    source.write_bytes(b'unchanged original scene')
    original = dict(state='READY', origin='X:/protected/original' + suffix,
                    source_path=str(source), worker_id='old-worker')
    workspace.record_scene_binding(c.source, dcc, original)
    assert workspace.get_scene_binding(c.source, dcc) == original
    seal(c)
    move.relocate_workspace(c.params, c.context)
    native = c.target / '.info/run_state.json'
    raw_before = native.read_bytes()
    current = c.target / source.name

    assert workspace.get_scene_binding(c.target, dcc) == {**original, 'source_path': str(current)}
    assert current.read_bytes() == b'unchanged original scene'
    specs, _ = snapshot()
    api_id = next(key for key, spec in specs.items() if spec['executor'] == dcc)
    payload = dict(project='ysj', workspace_project='default', workspace_id='TARGET',
                   business_task_id='TARGET', scene_action='continue')
    recorded_source = _workflow_source_from_existing_owner({'steps': [{'api_id': api_id}]}, payload, {})
    assert recorded_source == str(current) and Path(recorded_source).exists()
    assert workspace._read(native)['scene_hosts'][dcc] == original
    assert native.read_bytes() == raw_before
    assert workspace.get_scene_binding(c.target, 'unknown') == {}


@pytest.mark.parametrize('source', ['X:/protected/original.ma', 'Z:/external/original.ma',
                                   'relative/original.ma', '', None])
def test_external_protected_relative_and_empty_sources_are_not_relocated(relocation_case, source):
    c = relocation_case
    original = dict(state='READY', origin=str(c.source / 'original.ma'), source_path=source,
                    worker_id='original-worker', custom={'preserved': True})
    workspace.record_scene_binding(c.source, 'maya', original)
    seal(c)
    move.relocate_workspace(c.params, c.context)
    native = c.target / '.info/run_state.json'
    raw_before = native.read_bytes()
    assert workspace.get_scene_binding(c.target, 'maya') == original
    assert native.read_bytes() == raw_before


def test_new_operations_keep_commit_verifier_recorded_source_and_old_id_recovery(relocation_case):
    c = relocation_case
    row, old_paths, audit = add_old_operation(c)
    original = dict(state='READY', origin=str(c.source / 'outputs/original.ma'),
                    source_path=str(c.source / 'outputs/original.ma'), worker_id='old-worker')
    workspace.record_scene_binding(c.source, 'maya', original)
    original_records = {key: path.read_bytes() for key, path in old_paths.items()
                        if key != 'operation_dir' and path.exists()}
    original_audit = audit.read_bytes()
    workspace._render_summary(c.source, workspace._read(c.source / '.info/run_state.json'))
    seal(c)
    receipt = move.relocate_workspace(c.params, c.context)
    payload = dict(task_id='api-after-move', project='ysj', workspace_project='default',
                   workspace_id='TARGET', business_task_id='TARGET')
    workspace.start_operation(payload, 'api')
    workspace.finish_operation(payload, 'SUCCESS')
    proof = move.verify_relocation_commit(c.maintenance, 'api-relocate',
        receipt['output']['receipt_manifest_relpath'], receipt['output']['receipt_manifest_sha256'])
    assert proof['report_view_status'] == 'CURRENT'
    assert workspace.get_scene_binding(c.target, 'maya')['source_path'] == str(c.target / 'outputs/original.ma')
    assert workspace._read(c.target / '.info/run_state.json')['scene_hosts']['maya'] == original
    from cgi_pipeline.server.internals import get_task_result
    recovered = get_task_result('api-old')
    assert recovered['status'] == 'SUCCESS' and recovered['terminal'] is True
    assert recovered['run_dir'] == str(c.target)
    assert move.recover_relocated_execution(row)['run_dir'] == str(c.target)
    assert not c.source.exists() and audit.read_bytes() == original_audit
    for key, content in original_records.items():
        assert workspace.operation_paths(c.target, 'api-old')[key].read_bytes() == content


def test_recorded_source_read_rejects_an_unverified_commit(relocation_case):
    c = relocation_case
    workspace.record_scene_binding(c.source, 'maya', {'source_path': str(c.source / 'outputs/original.ma')})
    seal(c)
    receipt = move.relocate_workspace(c.params, c.context)
    (c.target / receipt['output']['commit_relpath']).write_text('{}', encoding='utf-8')
    with pytest.raises(workspace.WorkspaceError, match='manifest identity differs'):
        workspace.get_scene_binding(c.target, 'maya')
