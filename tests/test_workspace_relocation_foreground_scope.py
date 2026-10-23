"""Human scope evidence is bounded; it cannot override observed ownership."""
from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from cgi_pipeline.contracts import ApiContext
from cgi_pipeline.core import workspace, workspace_relocation as move
from cgi_pipeline.core import workspace_relocation_evidence as evidence
from cgi_pipeline.core.workspace_adoption_evidence import digest_file, inventory, read_sealed
from test_workspace_relocation import existing_target, recover, relocation_case, root, seal


class ForegroundProcess:
    """A deterministic local process; no test queries or alters a real host."""

    def __init__(self, directory, pid=41, name='blender.exe'):
        self.pid = pid
        self.info = dict(pid=pid, name=name, create_time=1234567.25,
                         exe=str(directory / name))
        self.environment = {}
        self.command = [self.info['exe']]

    def create_time(self):
        return self.info['create_time']

    def exe(self):
        return self.info['exe']

    def name(self):
        return self.info['name']

    def environ(self):
        return dict(self.environment)

    def cmdline(self):
        return list(self.command)


@pytest.fixture
def foreground_case(relocation_case, monkeypatch):
    c = relocation_case
    c.processes = [ForegroundProcess(Path('C:/DccApps'))]
    c.sessions, c.queries = [], []
    monkeypatch.setattr(move, '_foreground_clear', evidence._foreground_clear)
    monkeypatch.setattr(psutil, 'process_iter', lambda *args, **kwargs: list(c.processes))
    monkeypatch.setattr(psutil, 'net_connections', lambda **kwargs: [])
    monkeypatch.setattr(psutil, 'Process', lambda pid: next(p for p in c.processes if p.pid == pid))
    monkeypatch.setattr('cgi_pipeline.execution.execute_api', lambda *args, **kwargs:
                        dict(status='SUCCESS', output={'sessions': deepcopy(c.sessions)}))

    def inspect(payload, sync):
        c.queries.append(payload)
        assert sync is True
        assert payload['api_id'] == 'maya.asset.maya_get_scene_info'
        assert payload['api_params'] == {}
        return deepcopy(c.scene_response)

    c.scene_response = dict(status='SUCCESS', detail={'output': {'result': dict(
        scene=dict(path=str(c.root / 'unrelated.ma'), modified=True),
        references=[], statistics={'references': 0})}})
    monkeypatch.setattr('cgi_pipeline.server.foreground_client.submit_foreground_task', inspect)
    return c


def confirmation(c):
    return dict(schema_version=1, kind='cgi_workspace_foreground_scope_confirmation',
        maintenance_root=str(c.maintenance), recorded_at=time.time() - 1,
        expires_at=time.time() + 120, statement='没有使用这些目录，迁移期间也不会使用',
        evidence='用户本轮明确确认：没有使用这些目录，迁移期间也不会使用',
        processes=[dict(pid=p.pid, create_time=p.create_time(), exe=p.exe()) for p in c.processes],
        scopes=[dict(source_root=str(c.source), target_root=str(c.target))])


def approve(c, value=None):
    c.manifest['foreground_confirmation'] = deepcopy(value if value is not None else confirmation(c))
    seal(c)
    return c.manifest['foreground_confirmation']


def clear(c, value=None, roots=None):
    return evidence._foreground_clear(roots or (c.source, c.target), 'api-check', 'ysj',
                                      value, c.maintenance)


def test_unknown_unregistered_process_without_confirmation_still_blocks(foreground_case):
    c = foreground_case
    before = inventory(c.source)
    with pytest.raises(workspace.WorkspaceError):
        move.relocate_workspace({**c.params, 'mode': 'preview'}, c.context)
    assert inventory(c.source) == before and not c.target.exists()


def test_preview_records_operator_statement_without_claiming_machine_inspection(foreground_case):
    c = foreground_case
    approved = approve(c)
    before = inventory(c.source)
    result = move.relocate_workspace({**c.params, 'mode': 'preview'}, c.context)['output']
    assert result['preview'] is True and result['plan_sha256'] == c.params['manifest_sha256']
    rows = result['foreground_evidence']
    human = [row for row in rows if row['evidence_kind'] == 'operator_recorded_confirmation']
    assert len(human) == 1
    for field in ('statement', 'evidence', 'recorded_at', 'expires_at', 'scopes', 'maintenance_root'):
        assert human[0][field] == approved[field]
    assert all(human[0][field] == approved['processes'][0][field] for field in ('pid', 'create_time', 'exe'))
    assert inventory(c.source) == before and not c.target.exists()
    assert c.queries == []


def test_commit_seals_the_same_foreground_evidence_and_original_manifest(foreground_case):
    c = foreground_case
    approved = approve(c)
    output = move.relocate_workspace(c.params, c.context)['output']
    proof = read_sealed(c.maintenance, output['receipt_manifest_relpath'], output['receipt_manifest_sha256'])
    assert proof['approved_manifest_sha256'] == c.params['manifest_sha256']
    assert proof['foreground_evidence'] == output['foreground_evidence']
    assert proof['foreground_evidence'][0]['evidence_kind'] == 'operator_recorded_confirmation'
    assert proof['foreground_evidence'][0]['statement'] == approved['statement']
    assert recover(c)['status'] == 'SUCCESS'
    assert not c.source.exists() and (c.target / 'outputs/original.ma').is_file()


def test_human_statement_cannot_be_changed_after_manifest_sealing(foreground_case):
    c = foreground_case
    approve(c)
    c.manifest['foreground_confirmation']['statement'] = 'different permission'
    (c.maintenance / c.params['manifest_relpath']).write_bytes(evidence.canonical(c.manifest))
    before = inventory(c.source)
    with pytest.raises(workspace.WorkspaceError):
        move.relocate_workspace(c.params, c.context)
    assert inventory(c.source) == before and not c.target.exists()


@pytest.mark.parametrize('value', [None, False, [], ''])
def test_present_confirmation_requires_an_object_even_without_unknown_process(foreground_case, value):
    c = foreground_case
    c.processes.clear()
    c.manifest['foreground_confirmation'] = value
    seal(c)
    with pytest.raises(workspace.WorkspaceError):
        move.relocate_workspace({**c.params, 'mode': 'preview'}, c.context)


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('schema_version', 2), ('kind', 'operator_override'),
    ('maintenance_root', 'elsewhere'), ('recorded_at', True), ('expires_at', False),
    ('recorded_at', float('nan')), ('expires_at', float('inf')), ('statement', '  '),
    ('evidence', ''), ('evidence', []), ('processes', []), ('scopes', []),
    ('extra_authority', True),
])
def test_malformed_or_unbounded_confirmation_is_rejected(foreground_case, field, value):
    c = foreground_case
    approved = confirmation(c)
    approved[field] = value
    with pytest.raises(workspace.WorkspaceError):
        clear(c, approved)


@pytest.mark.parametrize('fault', ['expired', 'future', 'too_long', 'reversed', 'wrong_pid',
    'pid_bool', 'time_bool', 'time_nan', 'relative_exe', 'wrong_exe', 'wrong_time',
    'wrong_source', 'wrong_target', 'duplicate_process', 'duplicate_scope', 'process_extra', 'scope_extra'])
def test_confirmation_cannot_expand_identity_scope_or_time(foreground_case, fault):
    c = foreground_case
    approved = confirmation(c)
    process, scope = approved['processes'][0], approved['scopes'][0]
    now = time.time()
    if fault == 'expired':
        approved.update(recorded_at=now - 60, expires_at=now - 1)
    elif fault == 'future':
        approved.update(recorded_at=now + 60, expires_at=now + 120)
    elif fault == 'too_long':
        approved['expires_at'] = approved['recorded_at'] + 4 * 3600 + 1
    elif fault == 'reversed':
        approved['expires_at'] = approved['recorded_at'] - 1
    elif fault == 'wrong_pid':
        process['pid'] += 1
    elif fault == 'pid_bool':
        process['pid'] = True
    elif fault == 'time_bool':
        process['create_time'] = True
    elif fault == 'time_nan':
        process['create_time'] = float('nan')
    elif fault == 'relative_exe':
        process['exe'] = 'blender.exe'
    elif fault == 'wrong_exe':
        process['exe'] = str(c.root / 'another.exe')
    elif fault == 'wrong_time':
        process['create_time'] += 1
    elif fault in ('wrong_source', 'wrong_target'):
        scope['source_root' if fault == 'wrong_source' else 'target_root'] = str(c.root / 'other')
    elif fault == 'duplicate_process':
        approved['processes'].append(deepcopy(process))
    elif fault == 'duplicate_scope':
        approved['scopes'].append(deepcopy(scope))
    elif fault == 'process_extra':
        process['skip_runtime_check'] = True
    elif fault == 'scope_extra':
        scope['recursive_all'] = True
    with pytest.raises(workspace.WorkspaceError):
        clear(c, approved)


def test_one_confirmation_can_cover_explicit_batch_but_not_another_task(foreground_case):
    c = foreground_case
    approved = confirmation(c)
    other = (c.source.parent / 'OTHER', c.target.parent / 'OTHER')
    approved['scopes'].append(dict(source_root=str(other[0]), target_root=str(other[1])))
    assert clear(c, approved)
    assert clear(c, approved, other)
    with pytest.raises(workspace.WorkspaceError):
        clear(c, approved, (c.source.parent / 'OUTSIDE', c.target.parent / 'OUTSIDE'))


@pytest.mark.parametrize('change', ['new_pid', 'pid_reused', 'changed_exe'])
def test_current_process_changes_do_not_inherit_old_confirmation(foreground_case, change):
    c = foreground_case
    approved = confirmation(c)
    if change == 'new_pid':
        c.processes.append(ForegroundProcess(c.root, pid=42))
    elif change == 'pid_reused':
        c.processes[0].info['create_time'] += 1
    else:
        c.processes[0].info['exe'] = str(c.root / 'replacement.exe')
    with pytest.raises(workspace.WorkspaceError):
        clear(c, approved)


@pytest.mark.parametrize('mark', ['environment', 'host_command'])
def test_operator_confirmation_cannot_override_unowned_cgi_process(foreground_case, mark):
    c = foreground_case
    approved = confirmation(c)
    if mark == 'environment':
        c.processes[0].environment['CGI_RUNTIME_ID'] = 'orphan-generation'
    else:
        c.processes[0].command.append('cgi_pipeline.hosts.blender_host')
    with pytest.raises(workspace.WorkspaceError):
        clear(c, approved)


@pytest.mark.parametrize('argument', ['ordinary_install', 'ordinary_scene'])
def test_cgi_word_in_an_ordinary_path_is_not_a_runtime_ownership_marker(foreground_case, argument):
    c = foreground_case
    process = c.processes[0]
    if argument == 'ordinary_install':
        process.info['exe'] = str(c.root / 'portable/blender.exe')
        process.command = [process.exe()]
    else:
        process.command.append(str(c.root / 'ordinary_scene.blend'))
    assert clear(c, confirmation(c))[0]['evidence_kind'] == 'operator_recorded_confirmation'


def test_unrelated_cgi_configuration_does_not_claim_runtime_ownership(foreground_case):
    c = foreground_case
    c.processes[0].environment['CGI_COLOR_CONFIG'] = 'display-policy'
    assert clear(c, confirmation(c))[0]['evidence_kind'] == 'operator_recorded_confirmation'


@pytest.mark.parametrize('error', [psutil.AccessDenied, psutil.NoSuchProcess])
def test_unreadable_process_identity_is_not_covered_by_a_prior_statement(foreground_case, monkeypatch, error):
    c = foreground_case
    approved = confirmation(c)

    def cannot_read():
        raise error(41)

    monkeypatch.setattr(c.processes[0], 'exe', cannot_read)
    with pytest.raises(workspace.WorkspaceError):
        clear(c, approved)


def test_unreal_cannot_use_maya_blender_human_confirmation(foreground_case):
    c = foreground_case
    c.processes[0].info['name'] = 'UnrealEditor.exe'
    with pytest.raises(workspace.WorkspaceError):
        clear(c, confirmation(c))


@pytest.mark.parametrize('owned', ['maya_scene', 'maya_reference', 'blender_scene', 'unreadable_maya'])
def test_human_confirmation_never_overrides_registered_scene_reads(foreground_case, monkeypatch, owned):
    c = foreground_case
    c.processes[0].info['name'] = 'maya.exe' if owned.startswith('maya') or owned == 'unreadable_maya' else 'blender.exe'
    approved = confirmation(c)
    if owned == 'blender_scene':
        c.sessions.append(dict(dcc='blender', pid=41, file=str(c.source / 'outputs/original.ma')))
    else:
        c.sessions.append(dict(dcc='maya', port=7001, session_id='maya:7001'))
        monkeypatch.setattr(psutil, 'net_connections', lambda **kwargs: [SimpleNamespace(
            pid=41, laddr=SimpleNamespace(port=7001), status=psutil.CONN_LISTEN)])
        info = c.scene_response['detail']['output']['result']
        if owned == 'maya_scene':
            info['scene']['path'] = str(c.target / 'a.ma')
        elif owned == 'maya_reference':
            info.update(references=[str(c.source / 'outputs/original.ma')], statistics={'references': 1})
        else:
            c.scene_response['status'] = 'ERROR'
    with pytest.raises(workspace.WorkspaceError):
        clear(c, approved)
    assert all(row['api_id'] == 'maya.asset.maya_get_scene_info' for row in c.queries)


def test_unrelated_registered_foreground_is_machine_evidence(foreground_case, monkeypatch):
    c = foreground_case
    c.processes[0].info['name'] = 'maya.exe'
    c.sessions.append(dict(dcc='maya', port=7001, session_id='maya:7001'))
    monkeypatch.setattr(psutil, 'net_connections', lambda **kwargs: [SimpleNamespace(
        pid=41, laddr=SimpleNamespace(port=7001), status=psutil.CONN_LISTEN)])
    rows = clear(c, confirmation(c))
    assert len(rows) == 1 and rows[0]['evidence_kind'] == 'machine_inspected'
    assert rows[0]['scene'] == str(c.root / 'unrelated.ma')
    assert 'statement' not in rows[0]


def test_runtime_ownership_unknown_cannot_be_replaced_by_human_confirmation(foreground_case):
    c = foreground_case
    approved = confirmation(c)
    c.ownership['unknown'].append('lost_runtime_records')
    with pytest.raises(workspace.WorkspaceError):
        clear(c, approved)


def test_human_statement_cannot_replace_failed_registered_discovery(foreground_case, monkeypatch):
    c = foreground_case
    monkeypatch.setattr('cgi_pipeline.execution.execute_api', lambda *args, **kwargs: dict(status='ERROR'))
    with pytest.raises(workspace.WorkspaceError):
        clear(c, confirmation(c))


def test_existing_target_owned_by_runtime_cannot_be_relocated(foreground_case):
    c = foreground_case
    c.ownership['expected_nodes'].append(dict(node='target@host', role='slot', queues=['slot_queue'],
        runtime_id='target-runtime', generation='one', project='default', workspace_id='TARGET'))
    c.activity['expected_nodes'] = deepcopy(c.ownership['expected_nodes'])
    existing_target(c)
    approve(c)
    seal(c, target=True)
    before_source, before_target = inventory(c.source), inventory(c.target)
    with pytest.raises(workspace.WorkspaceError):
        move.relocate_workspace(c.params, c.context)
    assert inventory(c.source) == before_source and inventory(c.target) == before_target


@pytest.mark.parametrize('change', ['identity', 'new_process', 'expired'])
def test_second_guard_rechecks_identity_and_confirmation_lease_before_rename(foreground_case, monkeypatch, change):
    c = foreground_case
    approved = confirmation(c)
    clock = [time.time()]
    approved.update(recorded_at=clock[0] - 1, expires_at=clock[0] + 60)
    from cgi_pipeline.core import workspace_relocation_foreground as foreground
    monkeypatch.setattr(foreground.time, 'time', lambda: clock[0])
    approve(c, approved)
    before = inventory(c.source)

    def after_preparation(stage, *args):
        if stage == 'prepared':
            if change == 'identity':
                c.processes[0].info['create_time'] += 1
            elif change == 'new_process':
                c.processes.append(ForegroundProcess(Path('C:/DccApps'), pid=42))
            else:
                clock[0] += 61

    with pytest.raises(workspace.WorkspaceError):
        move.relocate_workspace(c.params, c.context, checkpoint=after_preparation)
    assert inventory(c.source) == before and not c.target.exists()
    recovered = recover(c)
    assert recovered['status'] == 'BLOCKED' and recovered['rolled_back'] is True
    assert recovered['replayed'] is False


def test_confirmation_expiry_after_rename_prevents_commit_and_rolls_back(foreground_case, monkeypatch):
    c = foreground_case
    clock = [time.time()]
    approved = confirmation(c)
    approved.update(recorded_at=clock[0] - 1, expires_at=clock[0] + 60)
    from cgi_pipeline.core import workspace_relocation_foreground as foreground
    monkeypatch.setattr(foreground.time, 'time', lambda: clock[0])
    approve(c, approved)
    before = inventory(c.source)

    def expiry_after_rename(stage, *args):
        if stage == 'moved':
            assert not c.source.exists() and c.target.is_dir()
            clock[0] += 61

    with pytest.raises(workspace.WorkspaceError):
        move.relocate_workspace(c.params, c.context, checkpoint=expiry_after_rename)
    assert inventory(c.source) == before and not c.target.exists()
    assert recover(c)['rolled_back'] is True


@pytest.mark.parametrize('version,accepted', [('1.0.0', True), ('8.0.0', False)])
def test_native_receipt_version_allowlist(foreground_case, monkeypatch, version, accepted):
    c = foreground_case
    c.processes.clear()
    from cgi_pipeline.core import workspace_relocation as current
    real_make_receipt = current.make_api_receipt

    def make_versioned_receipt(api_id, api_version, *args, **kwargs):
        return real_make_receipt(api_id, version, *args, **kwargs)

    monkeypatch.setattr(current, 'make_api_receipt', make_versioned_receipt)
    output = move.relocate_workspace(c.params, c.context)['output']
    args = (c.maintenance, 'api-relocate', output['receipt_manifest_relpath'], output['receipt_manifest_sha256'])
    if accepted:
        assert move.verify_relocation_commit(*args)['receipt']['api_version'] == version
    else:
        with pytest.raises(workspace.WorkspaceError):
            move.verify_relocation_commit(*args)


def test_legacy_manifest_needs_no_confirmation_when_all_processes_are_accounted(foreground_case):
    c = foreground_case
    c.processes.clear()
    assert 'foreground_confirmation' not in c.manifest
    result = move.relocate_workspace(c.params, c.context)
    assert result['status'] == 'SUCCESS' and result['output']['foreground_evidence'] == []
    assert recover(c)['status'] == 'SUCCESS'


def interrupt_before_rollback(c, monkeypatch):
    from cgi_pipeline.core import workspace_relocation_recovery as recovery

    def lost_process(*args):
        raise RuntimeError('simulated process exit before rollback')

    def after_rename(stage, *args):
        if stage == 'moved':
            raise RuntimeError('simulated interruption after rename')

    with monkeypatch.context() as crash:
        crash.setattr(recovery, 'rollback_prepared', lost_process)
        with pytest.raises(workspace.WorkspaceError, match='Rollback requires'):
            move.relocate_workspace(c.params, c.context, checkpoint=after_rename)
    assert not c.source.exists() and c.target.exists()
    assert recover(c)['status'] == 'NEEDS_ATTENTION'


@pytest.mark.parametrize('state', ['valid', 'expired_all_clear', 'expired_unknown', 'replacement_cannot_renew'])
def test_reconcile_uses_only_original_statement_and_expiry_never_renews_it(foreground_case, monkeypatch, state):
    c = foreground_case
    clock = [time.time()]
    approved = confirmation(c)
    approved.update(recorded_at=clock[0] - 1, expires_at=clock[0] + 60)
    from cgi_pipeline.core import workspace_relocation_foreground as foreground
    monkeypatch.setattr(foreground.time, 'time', lambda: clock[0])
    approve(c, approved)
    before = inventory(c.source)
    interrupt_before_rollback(c, monkeypatch)
    if state != 'valid':
        clock[0] += 61
    if state == 'expired_all_clear':
        c.processes.clear()
    c.activity.update(captured_at_epoch=clock[0], expires_at_epoch=clock[0] + 299)
    params = {**c.params, 'mode': 'reconcile', 'original_task_id': 'api-relocate'}
    for prefix, project, directory in (('', 'ysj', c.source), ('target_', 'default', c.target)):
        activity = {**deepcopy(c.activity), 'target_project': project, 'workspace_root': str(directory)}
        path = c.maintenance / (prefix + 'reconcile_activity.json')
        path.write_bytes(evidence.canonical(activity))
        params.update({prefix + 'activity_relpath': path.name, prefix + 'activity_sha256': digest_file(path)})
    if state == 'replacement_cannot_renew':
        params.update(manifest_relpath='replacement.json', manifest_sha256='f' * 64,
                      foreground_confirmation=confirmation(c))
    context = ApiContext.from_mapping(dict(project='default', task_id='api-explicit-reconcile',
        workspace_id='MAINT', business_task_id='MAINT'), 'pipeline')
    if state in ('expired_unknown', 'replacement_cannot_renew'):
        with pytest.raises(workspace.WorkspaceError):
            move.relocate_workspace(params, context)
        assert not c.source.exists() and c.target.exists()
    else:
        result = move.relocate_workspace(params, context)
        assert result['output']['rolled_back'] is True and result['output']['replayed'] is False
        assert inventory(c.source) == before and not c.target.exists()


@pytest.mark.parametrize('field,value', [('schema_version', True), ('maintenance_root', 'elsewhere'),
                                      ('scopes', []), ('kind', 'different')])
def test_recovery_expiry_fallback_cannot_hide_invalid_original_scope(foreground_case, field, value):
    c = foreground_case
    approved = confirmation(c)
    approved.update(recorded_at=time.time() - 120, expires_at=time.time() - 60)
    approved[field] = value
    from cgi_pipeline.core.workspace_relocation_foreground import validate_confirmation
    with pytest.raises(workspace.WorkspaceError):
        validate_confirmation(approved, (c.source, c.target), c.maintenance, allow_expired=True)
