"""Real filesystem transactions, with a deterministic idle service boundary."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgi_pipeline.catalog import snapshot
from cgi_pipeline.contracts import ApiContext
from cgi_pipeline.core import workspace, run_archive, workspace_relocation as move
from cgi_pipeline.core import workspace_relocation_evidence as evidence
from cgi_pipeline.core.workspace_adoption_evidence import inventory, digest_file
from test_workspace import root


def seal(case, *, target=False):
    case.ownership['known_task_ids'] = move_ids = evidence._operation_ids(case.source)
    case.activity.update(known_task_ids=move_ids)
    case.manifest.update(source_entries=inventory(case.source, ignored=move.LOCKS),
        source_identity_sha256=digest_file(case.source / '.info/run_state.json'),
        target_entries=inventory(case.target, ignored=move.LOCKS) if case.target.exists() else None)
    for key, value in [('manifest', case.manifest), ('activity', case.activity)]:
        path = case.maintenance / (key + '.json')
        path.write_bytes(evidence.canonical(value))
        case.params[key + '_relpath'] = path.name
        case.params[key + '_sha256'] = digest_file(path)
    if target:
        value = {**deepcopy(case.activity), 'target_project': 'default',
                 'workspace_root': str(case.target), 'known_task_ids': []}
        path = case.maintenance / 'target_activity.json'
        path.write_bytes(evidence.canonical(value))
        case.params.update(target_activity_relpath=path.name, target_activity_sha256=digest_file(path))


@pytest.fixture
def relocation_case(root, monkeypatch):
    from cgi_pipeline.server import internals
    monkeypatch.setattr(internals, 'AUDIT_DIR', root / 'audit')
    maintenance = root / 'projects/default/MAINT'
    source = root / 'projects/ysj/TARGET'
    target = root / 'projects/default/TARGET'
    for directory in (maintenance / '.info', source / '.info', source / 'outputs'):
        directory.mkdir(parents=True)
    workspace._write(maintenance / '.info/run_state.json', dict(wf_id='MAINT', project='default', business_task_id='MAINT'))
    workspace._write(source / '.info/run_state.json', dict(wf_id='TARGET', project='ysj', business_task_id='TARGET'))
    (source / 'outputs/original.ma').write_bytes(b'opaque production bytes\x00\xff')
    ownership = dict(sources=[], expected_nodes=[dict(node='legacy@host', role='legacy', queues=['cgi_queue'])],
        known_task_ids=[], known_tasks={}, ledger_activity=[], process_inventory=[], unknown=[])
    configuration = dict(identity=dict(host='127.0.0.1', port=6379, db=0), expected_queue_keys=['cgi_queue'])
    activity = dict(schema_version=1, kind='cgi_workspace_adoption_activity', target_project='ysj',
        business_task_id='TARGET', workspace_root=str(source), captured_at_epoch=time.time(),
        expires_at_epoch=time.time() + 299, **deepcopy(ownership),
        replies={key: {'legacy@host': [{'name': 'cgi_queue'}] if key == 'active_queues' else []}
                 for key in ('active', 'reserved', 'scheduled', 'active_queues')},
        broker=dict(transport='redis', **configuration, ready={'cgi_queue': []}, unacked={}, unacked_index={}))
    monkeypatch.setattr(evidence.probe, 'read_runtime_ownership', lambda ids: {**deepcopy(ownership), 'known_task_ids': list(ids)})
    monkeypatch.setattr(evidence.probe, 'broker_configuration', lambda *args: deepcopy(configuration))
    monkeypatch.setattr(evidence.probe, 'collect_activity_snapshot', lambda project, business, directory, **kwargs:
        {**deepcopy(activity), 'target_project': project, 'workspace_root': str(directory),
         'known_task_ids': list(kwargs.get('known_task_ids', []))})
    monkeypatch.setattr(move, '_foreground_clear', lambda *args: [])
    manifest = dict(schema_version=1, kind='cgi_workspace_relocation_manifest',
        source_project='ysj', target_project='default', business_task_id='TARGET',
        source_root=str(source), target_root=str(target), target_policy='refuse', business_project='ysj',
        original_allocation_receipt='')
    params = dict(mode='apply', source_project='ysj', target_project='default', business_task_id='TARGET',
        target_policy='refuse', target_activity_relpath='', target_activity_sha256='')
    context = ApiContext.from_mapping(dict(project='default', task_id='api-relocate',
        workspace_id='MAINT', business_task_id='MAINT'), 'pipeline')
    result = SimpleNamespace(root=root, maintenance=maintenance, source=source, target=target,
        ownership=ownership, configuration=configuration, activity=activity,
        manifest=manifest, params=params, context=context)
    seal(result)
    return result


def recover(case):
    from cgi_pipeline.server.internals import get_task_result
    return get_task_result('api-relocate')


def add_old_operation(case):
    paths = workspace.operation_paths(case.source, 'api-old')
    paths['operation_dir'].mkdir(parents=True)
    row = dict(task_id='api-old', workspace_id='TARGET', business_task_id='TARGET', project='ysj',
        run_dir=str(case.source), operation_dir=str(paths['operation_dir']), report_path=str(paths['report']),
        status='SUCCESS', api_id='test.original', submitted_at=1)
    workspace._write(paths['state'], row)
    workspace._write(paths['result'], {**row, 'receipt': {'artifacts': [str(case.source / 'outputs/original.ma')]}})
    paths['report'].write_text('# Original unchanged\n', encoding='utf-8')
    (case.root / 'audit').mkdir(exist_ok=True)
    audit = case.root / 'audit/api-old.json'
    audit.write_text(json.dumps({**row, 'scope': 'task'}) + '\n', encoding='utf-8')
    seal(case)
    return row, paths, audit


def test_single_registered_contract():
    specs, _ = snapshot()
    spec = specs[move.API_ID]
    assert spec['execution_strategy'] == 'control'
    assert spec['modes'] == ['preview', 'apply']
    assert spec['idempotent'] is False


def test_preview_has_no_rename_or_commit(relocation_case):
    c = relocation_case
    before = inventory(c.source)
    receipt = move.relocate_workspace({**c.params, 'mode': 'preview'}, c.context)
    assert receipt['output']['preview'] is True
    assert inventory(c.source) == before and not c.target.exists()
    assert not (c.maintenance / '.info/api-relocate_relocation.json').exists()


def test_commit_and_shared_notes_verifier(relocation_case):
    c = relocation_case
    old = inventory(c.source)
    receipt = move.relocate_workspace(c.params, c.context)
    assert not c.source.exists()
    assert (c.target / 'outputs/original.ma').read_bytes() == b'opaque production bytes\x00\xff'
    proof = move.verify_relocation_commit(c.maintenance, 'api-relocate',
        receipt['output']['receipt_manifest_relpath'], receipt['output']['receipt_manifest_sha256'])
    assert proof['old_binding']['project'] == 'ysj'
    assert proof['binding']['project'] == 'default' and proof['business_project'] == 'ysj'
    assert workspace.verify_workspace_identity(c.target, 'TARGET', 'default', 'TARGET', 'api-relocate')
    recovered = recover(c)
    assert recovered['status'] == 'SUCCESS' and recovered['receipt'] == receipt
    assert len(old) + 1 == len(inventory(c.target))


def test_old_audit_and_native_terminal_bytes_are_immutable(relocation_case):
    c = relocation_case
    row, paths, audit = add_old_operation(c)
    originals = {key: path.read_bytes() for key, path in paths.items() if key != 'operation_dir' and path.exists()}
    audit_before = audit.read_bytes()
    move.relocate_workspace(c.params, c.context)
    from cgi_pipeline.server.internals import get_task_result
    value = get_task_result('api-old')
    assert value['status'] == 'SUCCESS' and value['terminal'] is True
    assert value['run_dir'] == str(c.target)
    assert value['report_path'] == str(workspace.operation_paths(c.target, 'api-old')['report'])
    assert value['original_paths']['run_dir'] == str(c.source)
    assert audit.read_bytes() == audit_before
    for key, content in originals.items():
        assert workspace.operation_paths(c.target, 'api-old')[key].read_bytes() == content
    replay = move.recover_relocated_execution(row)
    assert replay['run_dir'] == str(c.target)
    assert replay['receipt']['artifacts'] == [str(c.source / 'outputs/original.ma')]


def existing_target(c):
    (c.target / 'work/tests').mkdir(parents=True)
    (c.target / 'temp').mkdir()
    (c.target / 'work/tests/legacy.bin').write_bytes(b'keep exact location')
    c.params['target_policy'] = c.manifest['target_policy'] = 'preserve_members'
    seal(c, target=True)


def test_existing_target_original_members_keep_exact_paths(relocation_case):
    c = relocation_case
    existing_target(c)
    original = inventory(c.target)
    move.relocate_workspace(c.params, c.context)
    for row in original:
        path = c.target / row['path']
        if row['kind'] == 'file':
            assert digest_file(path) == row['sha256']
        else:
            assert path.is_dir()
    assert not c.source.exists() and recover(c)['status'] == 'SUCCESS'


@pytest.mark.parametrize('point', ['prepared', 'moved', 'identity_written', 'member_moved'])
@pytest.mark.parametrize('existing', [False, True])
def test_precommit_failures_restore_both_roots(relocation_case, point, existing):
    c = relocation_case
    if point == 'member_moved' and not existing:
        pytest.skip('Member transaction applies only to an existing destination.')
    if existing:
        existing_target(c)
    old_source = inventory(c.source)
    old_target = inventory(c.target) if existing else None
    def fail(stage, *args):
        if stage == point:
            raise RuntimeError('injected precommit interruption')
    with pytest.raises(RuntimeError, match='interruption'):
        move.relocate_workspace(c.params, c.context, checkpoint=fail)
    assert inventory(c.source) == old_source
    assert (inventory(c.target) if existing else c.target.exists()) == (old_target if existing else False)
    assert recover(c)['status'] == 'BLOCKED' and recover(c)['rolled_back'] is True
    assert move.relocation_for_root(c.source) is None


@pytest.mark.parametrize('lost', ['after_commit', 'link_return'])
def test_lost_commit_reply_recovers_original_id(relocation_case, monkeypatch, lost):
    c = relocation_case
    if lost == 'link_return':
        real = move.os.link
        def link(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('lost kernel return')
        monkeypatch.setattr(move.os, 'link', link)
    def fail(stage, *args):
        if lost == 'after_commit' and stage == 'committed':
            raise OSError('lost caller reply')
    with pytest.raises(OSError):
        move.relocate_workspace(c.params, c.context, checkpoint=fail)
    assert recover(c)['status'] == 'SUCCESS'
    assert not c.source.exists()


@pytest.mark.parametrize('failure', ['source_drift', 'identity_drift', 'manifest_hash', 'target_exists', 'target_drift', 'collision', 'active', 'attempt', 'reparse'])
def test_preflight_rejects_without_moving_members(relocation_case, failure):
    c = relocation_case
    if failure in ('target_drift', 'collision'):
        existing_target(c)
    if failure == 'source_drift':
        (c.source / 'outputs/original.ma').write_bytes(b'changed')
    elif failure == 'identity_drift':
        workspace._write(c.source / '.info/run_state.json', dict(wf_id='OTHER', project='ysj', business_task_id='TARGET'))
    elif failure == 'manifest_hash':
        c.params['manifest_sha256'] = '0' * 64
    elif failure == 'target_exists':
        c.target.mkdir()
    elif failure == 'target_drift':
        (c.target / 'temp/new.txt').write_text('foreign', encoding='utf-8')
    elif failure == 'collision':
        (c.target / 'outputs').mkdir()
        seal(c, target=True)
    elif failure in ('active', 'attempt'):
        row, paths, _ = add_old_operation(c)
        if failure == 'active':
            row['status'] = 'RUNNING'
        else:
            row['execution_attempt'] = {'status': 'EXECUTING'}
        workspace._write(paths['state'], row)
        seal(c)
    elif failure == 'reparse':
        from test_workspace import symlink_or_skip
        symlink_or_skip(c.source / 'linked', c.maintenance, target_is_directory=True)
    before = (c.source / 'outputs/original.ma').read_bytes()
    with pytest.raises(workspace.WorkspaceError):
        move.relocate_workspace(c.params, c.context)
    assert (c.source / 'outputs/original.ma').read_bytes() == before
    assert not (c.target / '.info/run_state.json').exists()


@pytest.mark.parametrize('tamper', ['witness', 'identity', 'native_result', 'native_state', 'journal'])
def test_shared_verifier_rejects_commit_tampering(relocation_case, tamper):
    c = relocation_case
    output = move.relocate_workspace(c.params, c.context)['output']
    paths = workspace.operation_paths(c.maintenance, 'api-relocate')
    target = {'witness': c.target / output['commit_relpath'], 'identity': c.target / '.info/run_state.json',
        'native_result': paths['result'], 'native_state': paths['state'],
        'journal': c.maintenance / '.info/api-relocate_relocation_journal.json'}[tamper]
    value = workspace._read(target)
    if tamper == 'identity':
        value['project'] = 'ysj'
    elif tamper == 'journal':
        value['phase'] = 'ROLLED_BACK'
    else:
        value['tampered'] = True
    workspace._write(target, value)
    assert recover(c)['status'] == 'NEEDS_ATTENTION'
    if tamper == 'journal':
        with pytest.raises(workspace.WorkspaceError):
            workspace.start_operation(dict(task_id='api-forged-rollback', project='ysj', business_task_id='TARGET'), 'api')
        assert not c.source.exists()


def test_old_storage_cannot_be_recreated_and_new_storage_reuses_identity(relocation_case):
    c = relocation_case
    move.relocate_workspace(c.params, c.context)
    payload = dict(task_id='api-new', project='ysj', business_task_id='TARGET')
    with pytest.raises(workspace.WorkspaceError, match='committed storage'):
        workspace.start_operation(payload, 'api')
    assert not c.source.exists()
    workspace.start_operation({**payload, 'workspace_project': 'default'}, 'api')
    assert workspace._read(c.target / '.info/run_state.json')['wf_id'] == 'TARGET'


@pytest.mark.parametrize('runtime', ['missing', None, {'runtime_id': 'old-retired-generation'}])
def test_late_ack_reads_old_terminal_without_runtime_or_write(relocation_case, monkeypatch, runtime):
    c = relocation_case
    row, paths, _ = add_old_operation(c)
    move.relocate_workspace(c.params, c.context)
    from cgi_pipeline.core.runtime_operation import runtime_operation_guard
    called = []
    def never_execute(self, payload):
        called.append(payload)
        pytest.fail('An old operation must never replay after relocation.')
    guarded = runtime_operation_guard(never_execute, audit_path=lambda identifier: str(c.root / 'audit' / (identifier + '.json')))
    before = inventory(c.target)
    payload = dict(row)
    if runtime != 'missing':
        payload['runtime'] = runtime
    result = guarded(None, payload)
    assert result['status'] == 'SUCCESS' and result['run_dir'] == str(c.target)
    assert not called and inventory(c.target) == before


def test_native_reparse_attribute_is_rejected_without_symlink_permission(monkeypatch):
    from cgi_pipeline.core import workspace_adoption_evidence as local
    import stat
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400))
    with pytest.raises(workspace.WorkspaceError, match='Reparse'):
        local.local_stat(Path('pretend-junction'))


def test_storage_namespace_runtime_ownership_is_rejected(relocation_case):
    c = relocation_case
    c.ownership['expected_nodes'].append(dict(node='owned@host', role='slot', queues=['slot_queue'],
        runtime_id='owned', generation='one', project='ysj', workspace_id='TARGET'))
    # This is actual pool storage identity, independent of business config.
    c.activity['expected_nodes'] = deepcopy(c.ownership['expected_nodes'])
    seal(c)
    with pytest.raises(workspace.WorkspaceError, match='runtime'):
        move.relocate_workspace(c.params, c.context)


def test_effective_artifacts_outputs_and_current_report_links_preserve_history(relocation_case):
    c = relocation_case
    row, paths, audit = add_old_operation(c)
    artifact = c.source / 'outputs/original.ma'
    original = f'# Original details\n\n[Asset]({artifact.as_posix()})\n'
    paths['report'].write_text(original, encoding='utf-8')
    workspace._render_summary(c.source, workspace._read(c.source / '.info/run_state.json'))
    root_report = (c.source / 'REPORT.md').read_bytes()
    seal(c)
    output = move.relocate_workspace(c.params, c.context)['output']
    assert workspace.operation_paths(c.target, 'api-old')['report'].read_text(encoding='utf-8') == original
    backup = output['original_report_backup']
    assert (c.maintenance / backup['relpath']).read_bytes() == root_report
    rendered = (c.target / 'REPORT.md').read_text(encoding='utf-8')
    assert str(c.target / 'outputs/original.ma') in rendered
    assert artifact.as_posix() not in rendered
    sample = {**row, 'outputs': {'save.result': str(artifact)}, 'receipt': {'artifacts': [str(artifact)]}}
    projected = move.project_recovery(sample)
    assert projected['receipt'] == sample['receipt'] and projected['outputs'] == sample['outputs']
    assert projected['effective_outputs']['save.result'] == str(c.target / 'outputs/original.ma')
    assert projected['effective_artifacts'] == [dict(original=str(artifact), current=str(c.target / 'outputs/original.ma'), exists=True)]
    assert projected['current_workspace_report_path'] == str(c.target / 'REPORT.md')
    # Future ordinary root-report rendering retains the same current links.
    workspace._render_summary(c.target, workspace._read(c.target / '.info/run_state.json'))
    assert artifact.as_posix() not in (c.target / 'REPORT.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('when', ['before_rename', 'before_commit'])
def test_activity_lease_must_still_be_live_at_mutation_boundaries(relocation_case, monkeypatch, when):
    c = relocation_case
    from cgi_pipeline.core import workspace_activity
    clock, calls = [c.activity['captured_at_epoch'] + 1], []
    monkeypatch.setattr(workspace_activity, 'time_now', lambda: clock[0])
    def foreground(*args):
        calls.append(True)
        if when == 'before_rename' and len(calls) == 2:
            clock[0] = c.activity['expires_at_epoch'] + 1
    monkeypatch.setattr(move, '_foreground_clear', foreground)
    def checkpoint(stage, *args):
        if when == 'before_commit' and stage == 'moved':
            clock[0] = c.activity['expires_at_epoch'] + 1
    old = inventory(c.source)
    with pytest.raises(workspace.WorkspaceError, match='expired'):
        move.relocate_workspace(c.params, c.context, checkpoint=checkpoint)
    assert inventory(c.source) == old and not c.target.exists()


def test_proven_rollback_allows_new_explicit_id_without_replaying_old(relocation_case):
    c = relocation_case
    def fail(stage, *args):
        if stage == 'prepared':
            raise RuntimeError('first operation interrupted')
    with pytest.raises(RuntimeError):
        move.relocate_workspace(c.params, c.context, checkpoint=fail)
    assert recover(c)['rolled_back'] is True
    seal(c)
    context = ApiContext.from_mapping(dict(project='default', task_id='api-second-explicit',
        workspace_id='MAINT', business_task_id='MAINT'), 'pipeline')
    result = move.relocate_workspace(c.params, context)
    assert result['output']['relocation_task_id'] == 'api-second-explicit'
    assert recover(c)['rolled_back'] is True
    assert move.relocation_for_root(c.source)['task_id'] == 'api-second-explicit'


def test_committed_reply_loss_exposes_pending_view_then_explicit_reconcile_finishes_it(relocation_case):
    c = relocation_case
    row, paths, _ = add_old_operation(c)
    artifact = c.source / 'outputs/original.ma'
    paths['report'].write_text(f'[Original]({artifact.as_posix()})\n', encoding='utf-8')
    workspace._render_summary(c.source, workspace._read(c.source / '.info/run_state.json'))
    seal(c)
    def lost(stage, *args):
        if stage == 'committed':
            raise ConnectionError('reply disappeared after durable commit')
    with pytest.raises(ConnectionError):
        move.relocate_workspace(c.params, c.context, checkpoint=lost)
    assert recover(c)['status'] == 'SUCCESS' and recover(c)['report_view_status'] == 'PENDING'
    context = ApiContext.from_mapping(dict(project='default', task_id='api-view-reconcile',
        workspace_id='MAINT', business_task_id='MAINT'), 'pipeline')
    result = move.relocate_workspace({**c.params, 'mode': 'reconcile', 'original_task_id': 'api-relocate'}, context)
    assert result['output']['committed'] is True and result['output']['replayed'] is False
    assert recover(c)['report_view_status'] == 'CURRENT'
    assert artifact.as_posix() not in (c.target / 'REPORT.md').read_text(encoding='utf-8')
    projected = move.project_recovery({**row, 'receipt': {'artifacts': [(c.source / 'outputs/original.ma').as_uri()]}})
    assert projected['effective_artifacts'][0]['exists'] is True
