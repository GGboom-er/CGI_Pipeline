"""Regression checks for attribution and complete process coverage (no broker/DCC)."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cgi_pipeline.core import workspace_activity as activity, workspace_activity_probe as probe
from cgi_pipeline.core import workspace
from cgi_pipeline.core import workspace_adoption_evidence as evidence
from test_workspace import root
from test_workspace_adoption import case


def test_canonical_target_path_cannot_hide_behind_valid_foreign_owner(root):
    foreign = root / 'projects' / 'default' / 'FOREIGN'
    (foreign / '.info').mkdir(parents=True)
    workspace._write(foreign / '.info' / 'run_state.json',
                     dict(wf_id='FOREIGN', project='default', business_task_id='FOREIGN'))
    target = root / 'projects' / 'default' / 'TARGET'
    record = {'project': 'default', 'workspace_id': 'FOREIGN', 'business_task_id': 'FOREIGN',
              'source_path': str(target.parent / 'FOREIGN' / '..' / 'TARGET' / 'work' / 'file')}
    assert activity.classify(record, 'default', 'TARGET', target) == 'target'


def test_unregistered_cgi_worker_is_unknown_but_ordinary_process_is_ignored(monkeypatch):
    processes = [SimpleNamespace(info={'pid': 77, 'name': 'python.exe',
                  'cmdline': ['python', '-m', 'celery', '-A', 'cgi_pipeline.core.tasks', 'worker']})]
    monkeypatch.setattr(probe.psutil, 'process_iter', lambda attrs: processes)
    with pytest.raises(workspace.WorkspaceError):
        probe.reconcile_processes({}, {}, {})
    processes[0].info['cmdline'] = ['python', 'ordinary.py']
    processes[0].environ = lambda: {}
    probe.reconcile_processes({}, {}, {})


def validate(case, value=None, ownership=None, now=None):
    return activity.validate_activity(value or case.activity, 'default', 'TARGET', case.target,
        ownership or case.ownership, case.configuration, now=now)


@pytest.mark.parametrize('kind', activity.REPLIES)
def test_every_expected_node_must_reply_in_all_four_classes(case, kind):
    value = deepcopy(case.activity)
    value['replies'][kind] = {}
    with pytest.raises(workspace.WorkspaceError):
        validate(case, value)


@pytest.mark.parametrize('field', ['sources', 'expected_nodes', 'known_task_ids', 'known_tasks', 'process_inventory'])
def test_source_owner_terminal_or_process_identity_drift_invalidates_scope(case, field):
    ownership = deepcopy(case.ownership)
    ownership[field] = 'changed'
    with pytest.raises(workspace.WorkspaceError):
        validate(case, ownership=ownership)


@pytest.mark.parametrize('shape', ['target', 'unknown', 'literal_code'])
def test_active_target_unknown_and_nonliteral_python_payloads_refuse(case, shape):
    value = deepcopy(case.activity)
    kwargs = {'project': 'default', 'workspace_id': 'TARGET'} if shape == 'target' else {'task_id': 'unattributed'}
    value['replies']['active']['legacy@host'] = [dict(args=[], kwargs=kwargs)]
    if shape == 'literal_code':
        value['replies']['active']['legacy@host'][0]['args'] = "__import__('os').system('false')"
    with pytest.raises(workspace.WorkspaceError):
        validate(case, value)


def broker_message(body, content_type='application/json'):
    import base64
    import json
    return json.dumps({'body': base64.b64encode(json.dumps(body).encode()).decode(),
        'content-type': content_type, 'content-encoding': 'utf-8',
        'properties': {'body_encoding': 'base64'}, 'headers': {}})


@pytest.mark.parametrize('shape', ['target_ready', 'unknown_ready', 'unknown_list', 'missing_priority',
                                  'target_unacked', 'index_missing', 'pickle'])
def test_all_priority_lists_and_unacked_records_need_safe_owner(case, shape):
    import json
    value = deepcopy(case.activity)
    broker = value['broker']
    target = broker_message([[], {'project': 'default', 'workspace_id': 'TARGET'}, {}])
    if shape == 'target_ready':
        broker['ready']['cgi_queue'] = [target]
    elif shape == 'unknown_ready':
        broker['ready']['cgi_queue'] = [broker_message([[], {}, {}])]
    elif shape == 'unknown_list':
        broker['ready']['unregistered_queue'] = []
    elif shape == 'missing_priority':
        broker['expected_queue_keys'].append('cgi_queue\x06\x163')
    elif shape in ('target_unacked', 'index_missing'):
        broker['unacked']['tag'] = json.dumps([json.loads(target), 'exchange', 'routing'])
        broker['unacked_index'] = {'tag': 123} if shape == 'target_unacked' else {}
    elif shape == 'pickle':
        broker['ready']['cgi_queue'] = [broker_message({}, 'application/x-python-serialize')]
    with pytest.raises(workspace.WorkspaceError):
        validate(case, value)


def test_confirmed_foreign_task_progress_is_allowed_without_global_audit_lock(case):
    foreign = case.root / 'projects/default/FOREIGN'
    (foreign / '.info').mkdir(parents=True)
    workspace._write(foreign / '.info/run_state.json',
                     dict(wf_id='FOREIGN', project='default', business_task_id='FOREIGN'))
    value = deepcopy(case.activity)
    owner = dict(project='default', workspace_id='FOREIGN', business_task_id='FOREIGN')
    value['replies']['active']['legacy@host'] = [dict(args=[], kwargs=owner)]
    value['broker']['ready']['cgi_queue'] = [broker_message([[], owner, {}])]
    validate(case, value)


def ledger():
    return dict(version=1, lost={}, slots={'slot-1': dict(runtime_id='slot-1', generation='generation',
        root='Y:/managed/slot-1', queue='cgi_slot_1', project='default', workspace_id='FOREIGN',
        worker=dict(pid=42, create_time=1.25, cmdline=['worker']), state='IDLE', pending={}, active_task='',
        created_at=1, updated_at=1)})


def test_projection_allows_only_mutable_foreign_progress_not_resource_owner_drift():
    first = ledger()
    second = deepcopy(first)
    second['slots']['slot-1'].update(state='BUSY', active_task='some-operation', pending={'other': 2}, updated_at=3)
    assert activity.ownership_projection(first) == activity.ownership_projection(second)
    for field in ('generation', 'root', 'worker', 'workspace_id'):
        changed = deepcopy(first)
        changed['slots']['slot-1'][field] = 'changed'
        assert activity.ownership_projection(first) != activity.ownership_projection(changed)
    changed = deepcopy(first)
    changed['slots']['slot-2'] = deepcopy(changed['slots']['slot-1'])
    changed['slots']['slot-2']['runtime_id'] = 'slot-2'
    assert activity.ownership_projection(first) != activity.ownership_projection(changed)
    assert activity.ownership_projection(first) != activity.ownership_projection(dict(version=1, slots={}, lost={}))
    for field in ('unknown_schema',):
        changed['slots']['slot-1'][field] = True
        with pytest.raises(workspace.WorkspaceError):
            activity.ownership_projection(changed)


@pytest.mark.parametrize('value', [None, True, float('nan'), float('inf')])
def test_activity_ttl_must_be_finite_actual_time(case, value):
    modified = deepcopy(case.activity)
    modified['expires_at_epoch'] = value
    with pytest.raises(workspace.WorkspaceError):
        validate(case, modified)


@pytest.mark.parametrize('shape', ['future', 'expired', 'max_plus_one'])
def test_activity_lease_rejects_future_expired_and_over_maximum(case, shape):
    import time
    now = time.time()
    value = deepcopy(case.activity)
    start = now + 1 if shape == 'future' else now - 1
    end = now - .1 if shape == 'expired' else start + activity.MAX_ACTIVITY_LEASE_SEC
    if shape == 'max_plus_one':
        end = start + activity.MAX_ACTIVITY_LEASE_SEC + 1
    value.update(captured_at_epoch=start, expires_at_epoch=end)
    with pytest.raises(workspace.WorkspaceError):
        validate(case, value, now=now)


def test_shared_validator_rechecks_actual_ttl_after_inventory(case, monkeypatch):
    from cgi_pipeline.core import workspace_adoption as adopt
    import time
    original = adopt.validate_inventory
    def crosses_expiry(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(activity, 'time_now', lambda: case.activity['expires_at_epoch'] + 1)
        return result
    monkeypatch.setattr(adopt, 'validate_inventory', crosses_expiry)
    with pytest.raises(workspace.WorkspaceError, match='expired'):
        adopt.validate_adoption_evidence(case.maintenance, 'MAINT', 'MAINT', *(
            case.params[key] for key in ('target_project', 'business_task_id', 'manifest_relpath',
                                        'manifest_sha256', 'activity_relpath', 'activity_sha256')))


@pytest.mark.parametrize('path', ['../outside.json', 'C:/outside.json', 'work\\evidence.json', 'work/../evidence.json'])
def test_sealed_evidence_paths_cannot_escape_or_alias(case, path):
    with pytest.raises(workspace.WorkspaceError):
        evidence.read_sealed(case.maintenance, path, '0' * 64)


def test_marked_orphan_host_and_inaccessible_cgi_environment_are_unknown(monkeypatch):
    process = SimpleNamespace(info={'pid': 77, 'name': 'maya.exe', 'cmdline': ['maya.exe']},
        environ=lambda: {'CGI_RUNTIME_ID': 'slot-unregistered', 'CGI_RUNTIME_ROOT': 'Y:/orphan',
                         'CGI_RUNTIME_GENERATION': 'unknown'})
    monkeypatch.setattr(probe.psutil, 'process_iter', lambda attrs: [process])
    with pytest.raises(workspace.WorkspaceError):
        probe.reconcile_processes({}, {}, {})
    def denied():
        raise probe.psutil.AccessDenied(77)
    process.environ = denied
    with pytest.raises(workspace.WorkspaceError):
        probe.reconcile_processes({}, {}, {})


def test_real_windows_directory_junction_is_rejected_without_touching_target(case):
    import os
    from pathlib import Path
    import _winapi
    external = case.root / 'junction-target'
    external.mkdir()
    sentinel = external / 'keep.txt'
    sentinel.write_bytes(b'unchanged junction destination')
    link = case.target / 'work' / 'junction'
    allowed = Path(os.environ['TEMP']).resolve()
    assert link.parent.resolve().is_relative_to(allowed)
    assert external.resolve().is_relative_to(allowed)
    _winapi.CreateJunction(str(external), str(link))
    try:
        assert link.lstat().st_file_attributes & 0x400
        with pytest.raises(workspace.WorkspaceError):
            evidence.inventory(case.target)
        assert sentinel.read_bytes() == b'unchanged junction destination'
    finally:
        assert link.parent.resolve().is_relative_to(allowed)
        assert link.lstat().st_file_attributes & 0x400 and link.resolve() == external.resolve()
        os.rmdir(link)  # Remove this junction only; never traverse its destination.
    assert sentinel.read_bytes() == b'unchanged junction destination'
