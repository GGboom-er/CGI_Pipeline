"""Read-only scope, activity and filesystem evidence for relocation."""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from filelock import FileLock
from cgi_pipeline.core import run_archive, workspace
from cgi_pipeline.core import workspace_activity_probe as probe
from cgi_pipeline.core.workspace_activity import validate_activity, check_ttl
from cgi_pipeline.core.workspace_adoption_evidence import (digest_file, file_bytes, inventory, local_chain, local_stat, read_sealed)
LOCKS = {'.info/workspace.lock', '.info/execution.lock'}


def refuse(code, message):
    raise workspace.WorkspaceError('WORKSPACE_RELOCATION_' + code, message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _durable(path, value):
    workspace._write(path, value)
    with path.open('r+b') as stream:
        os.fsync(stream.fileno())


def _exclusive(path, value):
    raw = canonical(value)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _root(project, business, *, exists=True):
    workspace._validate_id(business)
    if (not isinstance(project, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', project)
            or run_archive._safe_segment(business) != business):
        refuse('IDENTITY_INVALID', 'Unnormalized storage identity is required.')
    path = run_archive.PROJECT_ROOT / 'projects' / project / business
    local_chain(path if exists else path.parent)
    if path.absolute() != path.resolve():
        refuse('PATH_INVALID', 'Managed roots cannot redirect.')
    if exists and not path.is_dir():
        refuse('PATH_INVALID', 'Source must be an existing managed directory.')
    return path


@contextmanager
def workspace_fence(project, business):
    """Also used by submissions; the lock stays outside the renamed root."""
    workspace._validate_id(business)
    workspace.workspace_project({'workspace_project': project})
    base = run_archive.PROJECT_ROOT / 'projects'
    base.mkdir(parents=True, exist_ok=True)
    local_chain(base)
    path = base / f'.workspace_{project}_{business}.lock'
    if path.is_symlink():
        refuse('PATH_INVALID', 'Relocation fence cannot redirect.')
    with FileLock(str(path), timeout=30):
        yield


def _route_path(project, business):
    workspace._validate_id(business)
    workspace.workspace_project({'workspace_project': project})
    return run_archive.PROJECT_ROOT / 'projects' / f'.relocation_{project}_{business}.json'


def needs_workspace_fence(payload, kind, foreground):
    if foreground and not payload.get('workspace_id'):
        return False
    if workspace.audit_only_operation(payload, kind):
        return False
    # There cannot be an existing migratable source when projects is absent.
    # Keep target-validation failures and unknown-ID reads free of allocation.
    return (run_archive.PROJECT_ROOT / 'projects').exists()


def _identity(state):
    return {key: state.get(key) for key in ('wf_id', 'project', 'business_task_id', 'relocation')}


def _binding(root, state, receipt=''):
    return dict(schema_version=1, kind='cgi_workspace_binding', engine='cgi_pipeline',
        workspace_root=str(root), workspace_id=state['wf_id'], project=state['project'],
        workspace_project=state['project'], business_task_id=state['business_task_id'],
        run_state='.info/run_state.json', allocation_receipt=receipt)


def _operation_ids(root):
    info = root / '.info'
    ids = {p.name[:-len('_state.json')] for p in info.glob('*_state.json') if p.name != 'run_state.json'}
    ids.update(p.parent.name for p in (info / 'operations').glob('*/state.json'))
    return sorted(ids)


def _terminal_records(root):
    from cgi_pipeline.core.task_status import TERMINAL_STATUSES
    ids = _operation_ids(root)
    for identifier in ids:
        state = workspace._read(workspace.operation_paths(root, identifier)['state'])
        if state.get('status') not in TERMINAL_STATUSES:
            refuse('ACTIVE', f'Operation {identifier} is not terminal.')
        attempt = state.get('execution_attempt')
        if attempt and attempt.get('status') != 'TERMINAL':
            refuse('ACTIVE', f'Operation {identifier} has an unresolved attempt.')
    return ids


def _activity(root, project, business, sealed):
    ownership = probe.read_runtime_ownership(sealed['known_task_ids'])
    configuration = probe.broker_configuration(ownership['expected_nodes'])
    validate_activity(sealed, project, business, root, ownership, configuration)
    # Idle resident scenes still have path-sensitive ownership/dirty state. They
    # must be explicitly saved and released by their existing lifecycle owner.
    for node in ownership['expected_nodes']:
        if node.get('project') == project and node.get('workspace_id') == business:
            refuse('RUNTIME_OWNED', 'Release the exact owning runtime before relocation.')
    fresh = probe.collect_activity_snapshot(project, business, root, known_task_ids=sealed['known_task_ids'])
    validate_activity(fresh, project, business, root, ownership, configuration)
    return fresh


def check_approved_activity_ttl(maintenance, params, *, both=False):
    prefixes = ('', 'target_') if both or params.get('target_policy') == 'preserve_members' else ('',)
    for prefix in prefixes:
        activity = read_sealed(maintenance, params[prefix + 'activity_relpath'], params[prefix + 'activity_sha256'])
        check_ttl(activity)


def _foreground_clear(roots, task_id, business_project, confirmation=None, maintenance=None):
    """Use registered discovery and existing read-only foreground transport.

    Reachable endpoints alone are insufficient: every local interactive Maya
    or Blender process must be accounted for by a registered endpoint, runtime
    ownership or a sealed human statement for the exact unowned process/scope.
    Human statements never replace readable scene/reference or CGI ownership
    checks. No save, close or discard is performed.
    """
    import psutil
    from cgi_pipeline.execution import execute_api
    from cgi_pipeline.server.foreground_client import submit_foreground_task
    from cgi_pipeline.core.workspace_relocation_foreground import validate_confirmation, process_confirmation
    confirmation = validate_confirmation(confirmation, roots, maintenance)
    discovered = execute_api('pipeline.session.discover', {'dcc': 'all'},
                             {'project': business_project, 'execution_mode': 'background'})
    if discovered.get('status') != 'SUCCESS':
        refuse('FOREGROUND_UNKNOWN', 'Foreground discovery did not complete.')
    sessions = discovered.get('output', {}).get('sessions')
    if not isinstance(sessions, list):
        refuse('FOREGROUND_UNKNOWN', 'Foreground discovery has no complete session list.')
    ownership = probe.read_runtime_ownership(())
    if ownership.get('unknown'):
        refuse('FOREGROUND_UNKNOWN', 'Process ownership is incomplete.')
    accounted = {row['pid'] for row in ownership['process_inventory']}
    listening = {(row.pid, row.laddr.port) for row in psutil.net_connections(kind='tcp')
                 if row.status == psutil.CONN_LISTEN and row.laddr}
    maya_ports = {int(row['port']) for row in sessions if row.get('dcc') == 'maya'}
    blender_pids = {row.get('pid') for row in sessions if row.get('dcc') == 'blender'}
    result = []
    for process in psutil.process_iter(['pid', 'name']):
        pid, name = process.info['pid'], str(process.info.get('name') or '').lower()
        if pid in accounted:
            continue
        if name == 'maya.exe' and not any((pid, port) in listening for port in maya_ports):
            result.append(process_confirmation(process, confirmation))
        if name == 'blender.exe' and pid not in blender_pids:
            result.append(process_confirmation(process, confirmation))
        if name in ('unrealeditor.exe', 'ue4editor.exe'):
            refuse('FOREGROUND_UNKNOWN', 'Unowned Unreal foreground storage cannot be completely inspected.')
    for session in sessions:
        if session.get('dcc') == 'maya':
            port = int(session['port'])
            response = submit_foreground_task(dict(task_id=f'{task_id}_fg_{port}',
                api_id='maya.asset.maya_get_scene_info', api_params={}, project=business_project,
                api_context=dict(project=business_project, execution_mode='foreground', foreground_port=port)), sync=True)
            receipt = response.get('detail')
            if isinstance(receipt, str):
                receipt = json.loads(receipt)
            info = (receipt or {}).get('output', {}).get('result', {})
            scene = info.get('scene')
            if response.get('status') != 'SUCCESS' or not isinstance(scene, dict) or not isinstance(scene.get('path'), str):
                refuse('FOREGROUND_UNKNOWN', 'A Maya foreground scene could not be read completely.')
            path = scene['path']
            references = info.get('references', [])
            if (not isinstance(references, list) or
                    info.get('statistics', {}).get('references', len(references)) != len(references)):
                refuse('FOREGROUND_UNKNOWN', 'The scene overview truncated its reference list; ownership is incomplete.')
            for reference in references:
                referenced = Path(str(reference))
                if referenced.is_absolute() and any(workspace._resolved_path(referenced).is_relative_to(workspace._resolved_path(root)) for root in roots):
                    refuse('FOREGROUND_OWNED', 'An open foreground scene references a relocated root; leave it untouched.')
        elif session.get('dcc') == 'blender':
            # The discovery health query already reads bpy.data.filepath on
            # the native main thread and reports its owning process ID.
            path = session.get('file')
            if not isinstance(path, str):
                refuse('FOREGROUND_UNKNOWN', 'A Blender foreground scene path is unavailable.')
        else:
            continue
        result.append(dict(evidence_kind='machine_inspected', session_id=session.get('session_id'), scene=path))
        candidate = Path(path)
        if candidate.is_absolute() and any(workspace._resolved_path(candidate).is_relative_to(workspace._resolved_path(root)) for root in roots):
            refuse('FOREGROUND_OWNED', 'An open foreground scene belongs to a relocated root; leave it untouched until its owner releases it.')
    return result


def _scope(maintenance, params):
    source_project, target_project, business = (params[k] for k in
        ('source_project', 'target_project', 'business_task_id'))
    if source_project == target_project:
        refuse('IDENTITY_INVALID', 'Source and target storage must differ.')
    source = _root(source_project, business)
    target = _root(target_project, business, exists=False)
    if maintenance in (source, target):
        refuse('IDENTITY_INVALID', 'A separate bound maintenance workspace is required.')
    approved = read_sealed(maintenance, params['manifest_relpath'], params['manifest_sha256'])
    if (approved.get('schema_version') != 1 or approved.get('kind') != 'cgi_workspace_relocation_manifest'
            or any(approved.get(k) != params[k] for k in
                   ('source_project', 'target_project', 'business_task_id', 'target_policy'))
            or approved.get('source_root') != str(source) or approved.get('target_root') != str(target)):
        refuse('IDENTITY_INVALID', 'Sealed source/target identities do not match.')
    if 'foreground_confirmation' in approved and not isinstance(approved['foreground_confirmation'], dict):
        refuse('FOREGROUND_UNKNOWN', 'A supplied foreground confirmation must be an object.')
    _, state = workspace._validate_run_identity(source, business, source_project, business)
    if state.get('relocation'):
        refuse('UNSUPPORTED_CHAIN', 'A previously relocated workspace cannot be relocated again; original-ID route chains are not supported.')
    for proof_path in (source / '.info').glob('*_relocation.json'):
        value = workspace._read(proof_path)
        if value.get('kind') == 'cgi_workspace_relocation_commit' and value.get('maintenance_root') == str(source):
            refuse('MAINTENANCE_PINNED', 'This root owns durable relocation proofs and must remain at its current location.')
    if digest_file(source / '.info/run_state.json') != approved.get('source_identity_sha256'):
        refuse('DRIFT', 'Source identity changed after approval.')
    rows = inventory(source, ignored=LOCKS)
    if rows != approved.get('source_entries'):
        refuse('DRIFT', 'Source members or contents changed.')
    known = _terminal_records(source)
    activity = read_sealed(maintenance, params['activity_relpath'], params['activity_sha256'])
    if activity.get('known_task_ids') != known:
        refuse('ACTIVITY_UNKNOWN', 'Activity must cover every native historical operation.')
    _activity(source, source_project, business, activity)
    target_rows = None
    members = sorted(p.name for p in source.iterdir())
    if target.exists():
        local_chain(target)
        if params['target_policy'] != 'preserve_members':
            refuse('CONFLICT', 'An existing destination requires explicit member preservation.')
        target_rows = inventory(target, ignored=LOCKS)
        if target_rows != approved.get('target_entries'):
            refuse('DRIFT', 'Existing target members changed.')
        # This mode preserves each existing target member at its exact relative
        # path. No generic recursive merge, overwrite or reclassification.
        target_names = {p.name.casefold() for p in target.iterdir()}
        if any(name.casefold() in target_names for name in members):
            refuse('CONFLICT', 'Source and existing target top-level members collide.')
        target_activity = read_sealed(maintenance, params['target_activity_relpath'], params['target_activity_sha256'])
        if target_activity.get('known_task_ids') != []:
            refuse('ACTIVITY_UNKNOWN', 'Unbound target activity cannot omit native operations.')
        _activity(target, target_project, business, target_activity)
    elif approved.get('target_entries') is not None:
        refuse('DRIFT', 'The approved existing destination disappeared.')
    original_receipt = approved.get('original_allocation_receipt', '')
    workspace.verify_workspace_identity(source, business, source_project, business, original_receipt)
    return source, target, state, approved, members, target_rows, activity


