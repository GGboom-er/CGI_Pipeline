"""Explicit adoption never changes the contents or ownership of another task."""
import json
import hashlib
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.catalog import snapshot
from cgi_pipeline.contracts import ApiContext
from cgi_pipeline.core import workspace_adoption as adopt, workspace, run_archive
from cgi_pipeline.core import workspace_adoption_evidence as evidence
from cgi_pipeline.core.task_audit import read_adoption_route
from test_workspace import root


def test_adoption_has_one_registered_control_contract():
    specs, _ = snapshot()
    assert 'pipeline.workspace.adopt' in specs, 'No registered entry can adopt a nonempty unbound workspace.'
    assert specs['pipeline.workspace.adopt']['execution_strategy'] == 'control'
    assert set(specs['pipeline.workspace.adopt']['inputs']) == {
        'target_project', 'business_task_id', 'manifest_relpath', 'manifest_sha256',
        'activity_relpath', 'activity_sha256'}


@pytest.fixture
def case(root, monkeypatch):
    from cgi_pipeline.server import internals
    monkeypatch.setattr(internals, 'AUDIT_DIR', root / 'audit')
    maintenance = root / 'projects' / 'default' / 'MAINT'
    (maintenance / '.info').mkdir(parents=True)
    workspace._write(maintenance / '.info' / 'run_state.json',
        dict(wf_id='MAINT', project='default', business_task_id='MAINT'))
    target = root / 'projects' / 'default' / 'TARGET'
    (target / 'work').mkdir(parents=True)
    (target / 'temp').mkdir()
    (target / 'work' / 'original.txt').write_bytes(b'original legacy work\x00')
    rows = evidence.inventory(target)
    manifest = dict(schema_version=1, kind='cgi_workspace_adoption_manifest', target_project='default',
        business_task_id='TARGET', workspace_root=str(target), entries=rows,
        file_count=sum(row['kind'] == 'file' for row in rows),
        total_bytes=sum(row.get('size', 0) for row in rows))
    ownership = dict(sources=[], expected_nodes=[dict(node='legacy@host', role='legacy', queues=['cgi_queue'])],
        known_task_ids=[], known_tasks={}, ledger_activity=[], process_inventory=[], unknown=[])
    configuration = dict(identity=dict(host='127.0.0.1', port=6379, db=0), expected_queue_keys=['cgi_queue'])
    activity = dict(schema_version=1, kind='cgi_workspace_adoption_activity', target_project='default',
        business_task_id='TARGET', workspace_root=str(target), captured_at_epoch=time.time(),
        expires_at_epoch=time.time() + 119, **deepcopy(ownership),
        replies={key: {'legacy@host': [{'name': 'cgi_queue'}] if key == 'active_queues' else []}
                 for key in ('active', 'reserved', 'scheduled', 'active_queues')},
        broker=dict(transport='redis', **configuration, ready={'cgi_queue': []}, unacked={}, unacked_index={}))
    monkeypatch.setattr(adopt.probe, 'read_runtime_ownership', lambda *args: deepcopy(ownership))
    monkeypatch.setattr(adopt.probe, 'broker_configuration', lambda *args: deepcopy(configuration))
    monkeypatch.setattr(adopt.probe, 'collect_activity_snapshot', lambda *args, **kwargs: deepcopy(activity))
    params = dict(target_project='default', business_task_id='TARGET')
    for name, value in (('manifest', manifest), ('activity', activity)):
        path = maintenance / (name + '.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        params[name + '_relpath'] = path.name
        params[name + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    context = ApiContext.from_mapping(dict(project='default', task_id='api-original',
        workspace_id='MAINT', business_task_id='MAINT'), 'pipeline')
    return SimpleNamespace(root=root, target=target, maintenance=maintenance, context=context,
        params=params, activity=activity, ownership=ownership, configuration=configuration, rows=rows)


def query(case, task_id='api-original'):
    from cgi_pipeline.server.internals import get_task_result
    return get_task_result(task_id)


def assert_original(case):
    assert evidence.inventory(case.target, ignored=adopt._owned_names(case.target, 'api-original') |
                              {'.info/run_state.json'}) == case.rows


def test_commit_is_native_original_receipt_and_preserves_every_original_file(case):
    receipt = adopt.adopt_workspace(case.params, case.context)
    assert receipt['status'] == 'SUCCESS'
    assert receipt['output']['binding']['allocation_receipt'] == 'api-original'
    assert workspace.operation_paths(case.target, 'api-original')['operation_dir'] == (
        case.target / '.info/operations/api-original')
    assert workspace.verify_workspace_identity(case.target, 'TARGET', 'default', 'TARGET', 'api-original')
    recovered = query(case)
    assert recovered['receipt'] == receipt and recovered['status'] == 'SUCCESS'
    assert_original(case)


def test_legacy_adoption_commit_witness_recovers_without_rewriting_any_file(case):
    # Assemble a real flat-layout fixture from this test's nested commit. The
    # application recovery itself must not migrate or repair either layout.
    receipt=adopt.adopt_workspace(case.params, case.context)
    nested=workspace.operation_paths(case.target,'api-original')
    info=case.target/'.info'
    legacy={'operation_dir':info, **{key:info/f'api-original_{name}' for key,name in
        (('state','state.json'),('result','result.json'),('report','REPORT.md'))}}
    record=workspace._read(nested['state'])
    record.update(operation_dir=str(legacy['operation_dir']),report_path=str(legacy['report']))
    workspace._write(legacy['state'],record)
    receipt['artifacts']=[str(legacy['report'])]
    workspace._write(legacy['result'],receipt)
    legacy['report'].write_bytes(nested['report'].read_bytes())
    native=workspace._read(case.target/'.info/run_state.json')
    route=native['adoption']['route']
    route['operation_dir']=str(legacy['operation_dir'])
    native['adoption']['files']={name:adopt.digest_file(legacy[key]) for key,name in
        (('state','state.json'),('result','result.json'),('report','REPORT.md'))}
    workspace._write(case.target/'.info/run_state.json',native)
    # Remove only this isolated fixture's original nested records.
    for key in ('state','result','report'):
        nested[key].unlink()
    nested['operation_dir'].rmdir()
    nested['operation_dir'].parent.rmdir()
    audit=case.root/'audit/api-original.json'
    audit.write_text(json.dumps(route)+'\n',encoding='utf-8')
    before={str(p.relative_to(case.target)):p.read_bytes() for p in case.target.rglob('*') if p.is_file()}
    recovered=adopt.recover_adoption('api-original',route)
    assert recovered['status']=='SUCCESS'
    assert recovered['receipt']==receipt
    assert recovered['report_path']==str(legacy['report'])
    assert {str(p.relative_to(case.target)):p.read_bytes() for p in case.target.rglob('*') if p.is_file()}==before


def test_recovery_rejects_two_operation_layouts_without_rewriting_either(case):
    adopt.adopt_workspace(case.params, case.context)
    (case.target/'.info/api-original_REPORT.md').write_bytes(b'conflicting flat report')
    before=evidence.inventory(case.target)
    assert query(case)['status']=='NEEDS_ATTENTION'
    assert evidence.inventory(case.target)==before


@pytest.mark.parametrize('directory', [False, True])
def test_unknown_nested_children_prevent_rollback_of_all_metadata(case, directory):
    observed={}
    def inject(stage, target, route):
        if stage=='prepared':
            foreign=Path(route['operation_dir'])/'unknown-owner'
            foreign.mkdir() if directory else foreign.write_bytes(b'foreign child')
            observed['rows']=evidence.inventory(target, ignored={'.info/workspace.lock'})
            raise RuntimeError('stop before commit')
    with pytest.raises(workspace.WorkspaceError, match='Unknown metadata children'):
        adopt.adopt_workspace(case.params, case.context, checkpoint=inject)
    assert evidence.inventory(case.target, ignored={'.info/workspace.lock'})==observed['rows']
    assert not (case.target/'.info/run_state.json').exists()
    assert (case.target/'work/original.txt').read_bytes()==b'original legacy work\x00'


def test_route_write_failure_never_publishes_identity(case, monkeypatch):
    def fail(*args):
        raise OSError('audit route unavailable')
    monkeypatch.setattr(adopt, 'prepare_adoption_route', fail)
    with pytest.raises(OSError):
        adopt.adopt_workspace(case.params, case.context)
    assert not (case.target / '.info').exists()
    assert evidence.inventory(case.target) == case.rows


@pytest.mark.parametrize('published', [False, True])
def test_identity_publication_failure_recovers_only_if_commit_witness_exists(case, monkeypatch, published):
    original = adopt._publish_identity
    def fail(path, state):
        if published:
            original(path, state)
        raise OSError('publication response failed')
    monkeypatch.setattr(adopt, '_publish_identity', fail)
    with pytest.raises(OSError):
        adopt.adopt_workspace(case.params, case.context)
    assert (query(case)['status'] == 'SUCCESS') is published
    if not published:
        assert evidence.inventory(case.target) == case.rows


@pytest.mark.parametrize('boundary', ['publish_entry', 'link_call'])
def test_late_identity_conflict_never_overwrites_and_preserves_both_candidates(case, monkeypatch, boundary):
    foreign = b'{"foreign_identity":"must survive"}'
    state_path = case.target / '.info/run_state.json'
    if boundary == 'publish_entry':
        original = adopt._publish_identity
        def inject(path, state):
            path.write_bytes(foreign)
            return original(path, state)
        monkeypatch.setattr(adopt, '_publish_identity', inject)
    else:
        original = adopt.os.link
        def inject(source, destination, **kwargs):
            Path(destination).write_bytes(foreign)
            return original(source, destination, **kwargs)
        monkeypatch.setattr(adopt.os, 'link', inject)
    with pytest.raises(workspace.WorkspaceError, match='conflict|identity|Identity'):
        adopt.adopt_workspace(case.params, case.context)
    assert state_path.read_bytes() == foreign
    candidate = workspace._read(case.target / '.info/run_state.adoption.tmp')
    assert candidate['adoption']['task_id'] == 'api-original'
    assert workspace._read(workspace.operation_paths(case.target, 'api-original')['state'])['status'] == 'SUCCESS'
    assert query(case)['status'] != 'SUCCESS'
    assert (case.target / 'work/original.txt').read_bytes() == b'original legacy work\x00'


def test_hardlink_commit_with_lost_os_return_recovers_same_original_receipt(case, monkeypatch):
    original = adopt.os.link
    def lost_return(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('kernel operation completed but caller lost the return')
    monkeypatch.setattr(adopt.os, 'link', lost_return)
    with pytest.raises(OSError):
        adopt.adopt_workspace(case.params, case.context)
    result = query(case)
    assert result['status'] == 'SUCCESS' and result['receipt']['output']['task_id'] == 'api-original'
    assert workspace._read(case.target / '.info/run_state.adoption.tmp')['adoption']['task_id'] == 'api-original'


def test_dangling_identity_junction_preserves_foreign_entry_candidate_and_work(case, monkeypatch):
    import os
    import _winapi
    state_path = case.target / '.info/run_state.json'
    destination = case.root / 'dangling-destination'
    allowed = Path(os.environ['TEMP']).resolve()
    original, observed = adopt._publish_identity, {}
    def inject(path, state):
        assert path.parent.resolve().is_relative_to(allowed)
        assert destination.parent.resolve().is_relative_to(allowed)
        destination.mkdir()
        _winapi.CreateJunction(str(destination), str(path))
        observed['link'] = os.readlink(path)
        observed['candidate'] = (json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        os.rmdir(destination)  # This fixture's empty destination only, making its junction dangling.
        return original(path, state)
    monkeypatch.setattr(adopt, '_publish_identity', inject)
    try:
        with pytest.raises((OSError, workspace.WorkspaceError)):
            adopt.adopt_workspace(case.params, case.context)
        assert os.path.lexists(state_path) and not state_path.exists()
        assert state_path.lstat().st_file_attributes & 0x400
        assert os.readlink(state_path) == observed['link']
        assert (case.target / '.info/run_state.adoption.tmp').read_bytes() == observed['candidate']
        assert workspace._read(case.target / '.info/run_state.adoption.tmp')['adoption']['task_id'] == 'api-original'
        assert query(case)['status'] != 'SUCCESS'
        assert (case.target / 'work/original.txt').read_bytes() == b'original legacy work\x00'
    finally:
        if os.path.lexists(state_path):
            assert state_path.parent.resolve().is_relative_to(allowed)
            assert state_path.lstat().st_file_attributes & 0x400
            assert os.readlink(state_path) == observed['link']
            os.rmdir(state_path)  # Remove our junction itself, without traversing its missing target.


def test_prepared_success_is_not_public_success_without_identity_commit(case):
    def fail(stage, target, route):
        if stage == 'prepared':
            assert workspace._read(workspace.operation_paths(target, 'api-original')['state'])['status'] == 'SUCCESS'
            assert query(case)['status'] != 'SUCCESS'
            raise RuntimeError('before commit')
    with pytest.raises(RuntimeError):
        adopt.adopt_workspace(case.params, case.context, checkpoint=fail)
    assert not (case.target / '.info').exists()
    assert query(case)['status'] == 'NEEDS_ATTENTION'
    assert evidence.inventory(case.target) == case.rows


@pytest.mark.parametrize('tail', ['{"status":"ERROR","scope":"task"}\n', '{"status":"ERROR"'])
def test_committed_native_receipt_beats_outer_error_or_partial_append(case, tail):
    receipt = adopt.adopt_workspace(case.params, case.context)
    with (case.root / 'audit/api-original.json').open('a', encoding='utf-8') as stream:
        stream.write(tail)
    assert query(case)['receipt'] == receipt


def test_postcommit_handler_exception_and_restart_without_redis_recover_original(case, monkeypatch):
    def fail(stage, target, route):
        if stage == 'committed':
            raise ConnectionError('HTTP response lost')
    with pytest.raises(ConnectionError):
        adopt.adopt_workspace(case.params, case.context, checkpoint=fail)
    route = read_adoption_route('api-original', case.root / 'audit')
    import importlib
    importlib.reload(adopt)
    monkeypatch.setattr('cgi_pipeline.core.progress.get_latest_state',
                        lambda *args: pytest.fail('committed lookup must not use Redis'))
    result = query(case)
    assert result['status'] == 'SUCCESS' and result['receipt']['output']['task_id'] == 'api-original'
    assert route['task_id'] == result['task_id']


def test_duplicate_returns_original_receipt_without_reexecuting_or_new_route(case, monkeypatch):
    receipt = adopt.adopt_workspace(case.params, case.context)
    monkeypatch.setattr(adopt, '_fresh', lambda *args: pytest.fail('duplicate must not execute adoption'))
    later = ApiContext.from_mapping(dict(project='default', task_id='api-later',
        workspace_id='MAINT', business_task_id='MAINT'), 'pipeline')
    assert adopt.adopt_workspace(case.params, later) == receipt
    assert not (case.root / 'audit/api-later.json').exists()
    with pytest.raises(workspace.WorkspaceError):
        adopt.adopt_workspace(dict(case.params, manifest_sha256='0' * 64), later)


@pytest.mark.parametrize('field,value', [('project', 'other'), ('task_id', 'api-other'),
                                      ('run_dir', 'C:/foreign'), ('operation_dir', 'C:/foreign')])
def test_recovery_rejects_mismatched_original_route(case, field, value):
    adopt.adopt_workspace(case.params, case.context)
    route = read_adoption_route('api-original', case.root / 'audit')
    assert adopt.recover_adoption('api-original', dict(route, **{field: value}))['status'] != 'SUCCESS'


@pytest.mark.parametrize('name', ['result.json', 'state.json', 'REPORT.md'])
def test_recovery_rejects_changed_receipt_state_or_report(case, name):
    adopt.adopt_workspace(case.params, case.context)
    path = workspace.operation_paths(case.target, 'api-original')[{'result.json':'result','state.json':'state','REPORT.md':'report'}[name]]
    path.write_text('{}', encoding='utf-8')
    assert query(case)['status'] != 'SUCCESS'


def test_inventory_change_during_precommit_rolls_back_only_new_metadata(case):
    def change(stage, target, route):
        if stage == 'prepared':
            (target / 'work/original.txt').write_bytes(b'changed by concurrent owner')
    with pytest.raises(workspace.WorkspaceError):
        adopt.adopt_workspace(case.params, case.context, checkpoint=change)
    assert not (case.target / '.info').exists()
    assert (case.target / 'work/original.txt').read_bytes() == b'changed by concurrent owner'


def test_actual_fresh_activity_rejects_target_before_any_metadata(case, monkeypatch):
    dirty = deepcopy(case.activity)
    dirty['replies']['active']['legacy@host'] = [dict(args=[], kwargs=dict(project='default', workspace_id='TARGET'))]
    monkeypatch.setattr(adopt.probe, 'collect_activity_snapshot', lambda *args, **kwargs: dirty)
    with pytest.raises(workspace.WorkspaceError):
        adopt.adopt_workspace(case.params, case.context)
    assert not (case.target / '.info').exists()


def test_maintenance_identity_mismatch_is_rejected_by_shared_owner(case):
    with pytest.raises(workspace.WorkspaceError):
        adopt.validate_adoption_evidence(case.maintenance, 'OTHER', 'MAINT', *(
            case.params[key] for key in ('target_project', 'business_task_id', 'manifest_relpath',
                                        'manifest_sha256', 'activity_relpath', 'activity_sha256')))


def test_inventory_restats_previously_hashed_files_at_end(case, monkeypatch):
    earlier, later = case.target / 'work/original.txt', case.target / 'work/z-last.txt'
    later.write_bytes(b'later')
    original = evidence.digest_file
    def racing(path):
        digest = original(path)
        if path == later:
            earlier.write_bytes(b'changed after its hash')
        return digest
    monkeypatch.setattr(evidence, 'digest_file', racing)
    with pytest.raises(workspace.WorkspaceError):
        evidence.inventory(case.target)


def test_expired_activity_at_commit_prevents_native_identity(case, monkeypatch):
    from cgi_pipeline.core import workspace_activity as activity
    def expires(stage, target, route):
        if stage == 'prepared':
            monkeypatch.setattr(activity, 'time_now', lambda: case.activity['expires_at_epoch'] + 1)
    with pytest.raises(workspace.WorkspaceError, match='expired'):
        adopt.adopt_workspace(case.params, case.context, checkpoint=expires)
    assert evidence.inventory(case.target) == case.rows


def test_unlisted_preexisting_reserved_metadata_cannot_be_overwritten(case):
    (case.target / '.info').mkdir()
    preserved = case.target / '.info/legacy.json'
    preserved.write_bytes(b'keep original metadata')
    with pytest.raises(workspace.WorkspaceError):
        adopt.adopt_workspace(case.params, case.context)
    assert preserved.read_bytes() == b'keep original metadata'


@pytest.mark.parametrize('name', ['REPORT.md', '.info/operations/api-original/result.json'])
def test_rollback_preserves_foreign_files_created_after_info_but_before_prepare(case, monkeypatch, name):
    original = adopt._validate
    foreign = case.target / name
    injected = False
    def inject(*args, **kwargs):
        nonlocal injected
        if kwargs.get('ignored') and not injected:
            foreign.parent.mkdir(parents=True, exist_ok=True)
            foreign.write_bytes(b'foreign object: never created by adoption')
            injected = True
        return original(*args, **kwargs)
    monkeypatch.setattr(adopt, '_validate', inject)
    with pytest.raises(Exception):
        adopt.adopt_workspace(case.params, case.context)
    assert injected and foreign.read_bytes() == b'foreign object: never created by adoption'


def test_changed_created_metadata_is_preserved_instead_of_cleaned(case):
    foreign = case.target / 'REPORT.md'
    def replace(stage, target, route):
        if stage == 'prepared':
            foreign.write_bytes(b'new owner of this report')
            raise RuntimeError('stop before commit')
    with pytest.raises(workspace.WorkspaceError, match='ownership'):
        adopt.adopt_workspace(case.params, case.context, checkpoint=replace)
    assert foreign.read_bytes() == b'new owner of this report'
    assert not (case.target / '.info/run_state.json').exists()


def test_normal_allocation_and_adoption_share_one_allocation_lock(case, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    prepared, release = threading.Event(), threading.Event()
    def hold(stage, target, route):
        if stage == 'prepared':
            prepared.set()
            assert release.wait(5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        adopting = pool.submit(adopt.adopt_workspace, case.params, case.context, checkpoint=hold)
        assert prepared.wait(5)
        allocating = pool.submit(run_archive.create_run_dir, 'TARGET', 'default', directory_name='TARGET',
                                 business_task_id='TARGET')
        time.sleep(.05)
        assert not allocating.done()
        release.set()
        assert adopting.result()['status'] == 'SUCCESS'
        assert allocating.result() == case.target
