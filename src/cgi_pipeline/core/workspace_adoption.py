"""Explicit sealed legacy-workspace adoption, with the native identity as commit witness."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from cgi_pipeline.contracts import make_api_receipt, validate_receipt
from cgi_pipeline.core import run_archive, workspace
from cgi_pipeline.core import workspace_activity_probe as probe
from cgi_pipeline.core.task_audit import prepare_adoption_route
from cgi_pipeline.core.workspace_activity import check_ttl, validate_activity
from cgi_pipeline.core.workspace_adoption_evidence import (
    digest_file, file_bytes, local_chain, local_stat, read_sealed, refuse, target_path,
    validate_inventory, _signature,
)

API_ID, VERSION = 'pipeline.workspace.adopt', '1.0.0'


def _validate(maintenance_root, maintenance_workspace_id, maintenance_business_task_id,
              target_project, business_task_id, manifest_relpath, manifest_sha256,
              activity_relpath, activity_sha256, *, now=None, ignored=()):
    maintenance = local_chain(Path(maintenance_root).absolute())
    state = workspace._read(maintenance / '.info' / 'run_state.json')
    workspace.verify_workspace_identity(maintenance, maintenance_workspace_id, state.get('project'),
                                        maintenance_business_task_id)
    target = target_path(target_project, business_task_id)
    if target == maintenance:
        refuse('IDENTITY_INVALID', 'Maintenance and target workspaces must be distinct.')
    activity = read_sealed(maintenance, activity_relpath, activity_sha256)
    check_ttl(activity, now)
    manifest = read_sealed(maintenance, manifest_relpath, manifest_sha256)
    validate_inventory(manifest, target, target_project, business_task_id, ignored=ignored,
                       deadline=time.monotonic() + 12)
    ownership = probe.read_runtime_ownership(activity.get('known_task_ids', ()))
    configuration = probe.broker_configuration(ownership['expected_nodes'])
    validate_activity(activity, target_project, business_task_id, target, ownership, configuration, now=now)
    check_ttl(activity, now)
    return {'target_root': target, 'manifest': manifest, 'activity': activity}


def validate_adoption_evidence(maintenance_root, maintenance_workspace_id, maintenance_business_task_id,
                               target_project, business_task_id, manifest_relpath, manifest_sha256,
                               activity_relpath, activity_sha256, *, now=None):
    """Uncached, read-only owner shared by the gate and the server; no broker IO."""
    return _validate(maintenance_root, maintenance_workspace_id, maintenance_business_task_id,
                     target_project, business_task_id, manifest_relpath, manifest_sha256,
                     activity_relpath, activity_sha256, now=now)


def _operation_directories(info, operation):
    if not operation.is_relative_to(info):
        refuse('PATH_INVALID', 'Operation metadata escaped its native directory.')
    directories = []
    while operation != info:
        directories.append(operation)
        operation = operation.parent
    return tuple(reversed(directories))


def _owned_names(target, task_id):
    paths = workspace.operation_paths(target, task_id)
    info = target / '.info'
    names = (info, info / 'workspace.lock', target / 'REPORT.md',
             *(_operation_directories(info, paths['operation_dir'])),
             *(paths[key] for key in ('state', 'result', 'report')))
    return {path.relative_to(target).as_posix() for path in names}


def _durable(path, value):
    workspace._write(path, value)
    with path.open('r+b') as stream:
        os.fsync(stream.fileno())


def _publish_identity(path, state):
    # Atomic no-replace publication on same-volume filesystems supporting hardlinks.
    # Windows NTFS and POSIX link both reject an existing destination; rename differs.
    temporary = path.with_name('run_state.adoption.tmp')
    if temporary.exists() or temporary.is_symlink():
        refuse('CONFLICT', 'An unfinished identity publication requires investigation.')
    signature, published = None, False
    payload = (json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    try:
        with temporary.open('xb') as stream:
            try:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                signature = _signature(os.fstat(stream.fileno()))
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            refuse('CONFLICT', 'Native identity conflict: preserve the existing identity and prepared candidate.')
        if _signature(local_stat(path))[:3] != signature[:3] or file_bytes(path) != payload:
            refuse('PUBLISH_UNKNOWN', 'Published identity differs from the original prepared candidate.')
        published = True
    finally:
        if signature is not None and temporary.exists():
            # Creating a hardlink legitimately changes POSIX ctime. Inode/type and
            # exact bytes prove this is still our candidate without ignoring edits.
            if _signature(local_stat(temporary))[:3] != signature[:3] or file_bytes(temporary) != payload:
                refuse('CLEANUP_UNKNOWN', 'Identity temporary was replaced; preserve it for inspection.')
            if published or not os.path.lexists(path):
                temporary.unlink()
            # Conflict or unknown return with a destination retains both names;
            # public recovery independently checks the native commit witness.


def _created_identity(path, flexible=False):
    value = local_stat(path)
    if path.is_dir() or flexible:
        return value.st_dev, value.st_ino, value.st_mode
    return _signature(value)


def _check_created(created):
    for path, (identity, flexible) in created.items():
        if _created_identity(path, flexible) != identity:
            refuse('CLEANUP_UNKNOWN', 'A created metadata object changed ownership; preserve it.')


def _cleanup_created(created):
    # Establish all ownership before removing even one object; never traverse unknown children.
    _check_created(created)
    for path in created:
        if path.is_dir() and any(child not in created for child in path.iterdir()):
            refuse('CLEANUP_UNKNOWN', 'Unknown metadata children prevent cleanup; preserve all evidence.')
    for path in sorted(created, key=lambda item: len(item.parts), reverse=True):
        if _created_identity(path, created[path][1]) != created[path][0]:
            refuse('CLEANUP_UNKNOWN', 'Created metadata changed during cleanup; preserve it.')
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()


def recover_adoption(task_id, route):
    """Read the actual committed receipt without Redis, replay or state repair."""
    try:
        if route.get('task_id') != task_id or route.get('kind') != 'workspace_adoption_route':
            refuse('RECOVERY_INVALID', 'Original operation route does not match.')
        project, business = route['project'], route['business_task_id']
        target = target_path(project, business)
        paths = workspace.operation_paths(target, task_id)
        operation = paths['operation_dir']
        workspace._validate_id(task_id)
        if (route.get('workspace_id') != business or route.get('run_dir') != str(target)
                or route.get('operation_dir') != str(operation)):
            refuse('RECOVERY_INVALID', 'Recovery route is outside its canonical workspace.')
        local_chain(operation)
        _, state = workspace._validate_run_identity(target, business, project, business)
        witness = state.get('adoption') or {}
        if witness.get('task_id') != task_id or witness.get('route') != route:
            refuse('RECOVERY_INVALID', 'Native identity has no matching adoption commit witness.')
        for key, name in (('state', 'state.json'), ('result', 'result.json'), ('report', 'REPORT.md')):
            if digest_file(paths[key]) != witness.get('files', {}).get(name):
                refuse('RECOVERY_INVALID', 'Prepared native result or report changed.')
        binding = workspace.verify_workspace_identity(target, business, project, business, task_id)
        receipt = json.loads(file_bytes(paths['result'], limit=1024 * 1024))
        validate_receipt(receipt, api_id=API_ID, api_version=VERSION)
        if (receipt.get('api_id') != API_ID or receipt.get('api_version') != VERSION
                or receipt.get('status') != 'SUCCESS' or receipt.get('output', {}).get('binding') != binding
                or receipt.get('output', {}).get('task_id') != task_id):
            refuse('RECOVERY_INVALID', 'Committed original receipt identity does not match.')
        return {'task_id': task_id, 'status': 'SUCCESS', 'terminal': True, 'api_id': API_ID,
                'receipt': receipt, **binding, 'run_dir': str(target), 'operation_dir': str(operation),
                'report_path': str(paths['report']), 'recovery_source': 'native_adoption_commit'}
    except (OSError, ValueError, KeyError, TypeError, workspace.WorkspaceError) as exc:
        return {'task_id': task_id, 'status': 'NEEDS_ATTENTION', 'terminal': False,
                'error_code': getattr(exc, 'code', 'WORKSPACE_ADOPTION_RECOVERY_INVALID'),
                'error': 'Original adoption has no verified committed receipt; do not replay it.'}


def _fresh(project, business, target, activity):
    fresh = probe.collect_activity_snapshot(project, business, target,
                                             known_task_ids=activity['known_task_ids'])
    ownership = probe.read_runtime_ownership(activity['known_task_ids'])
    configuration = probe.broker_configuration(ownership['expected_nodes'])
    validate_activity(fresh, project, business, target, ownership, configuration)
    # Fresh activity may change, resource ownership and immutable terminal sources may not.
    for key in ('sources', 'expected_nodes', 'known_task_ids', 'known_tasks', 'process_inventory'):
        if fresh[key] != activity[key]:
            refuse('ACTIVITY_DRIFT', 'Fresh service evidence changed the approved ownership scope.')


def adopt_workspace(params, context, *, checkpoint=None):
    """Only the registered server supplies operation identity and maintenance binding."""
    started = time.perf_counter()
    task_id = context.extras.get('task_id', '')
    workspace._validate_id(task_id)
    maintenance_id = context.extras.get('workspace_id', '')
    maintenance_business = context.extras.get('business_task_id', '')
    maintenance = target_path(context.project, maintenance_business)
    workspace.verify_workspace_identity(maintenance, maintenance_id, context.project, maintenance_business)
    args = (maintenance, maintenance_id, maintenance_business, *(params[key] for key in
            ('target_project', 'business_task_id', 'manifest_relpath', 'manifest_sha256',
             'activity_relpath', 'activity_sha256')))
    project, business = params['target_project'], params['business_task_id']
    target = target_path(project, business)
    with run_archive.allocation_lock():
        state_path = target / '.info' / 'run_state.json'
        if state_path.exists():
            state = workspace._read(state_path)
            witness = state.get('adoption') or {}
            if (witness.get('manifest_sha256') != params['manifest_sha256']
                    or witness.get('maintenance_root') != str(maintenance)
                    or witness.get('maintenance_workspace_id') != maintenance_id
                    or witness.get('maintenance_business_task_id') != maintenance_business):
                refuse('CONFLICT', 'Target already has a different native identity or adoption owner.')
            recovered = recover_adoption(witness.get('task_id'), witness.get('route') or {})
            if recovered.get('status') != 'SUCCESS':
                refuse('RECOVERY_INVALID', 'Existing commit cannot be recovered; do not replay it.')
            return recovered['receipt']
        evidence = _validate(*args)
        for name in ('.info', 'REPORT.md', 'REPORT.md.tmp'):
            if (target / name).exists() or (target / name).is_symlink():
                refuse('CONFLICT', 'Unbound target contains reserved native metadata.')
        _fresh(project, business, target, evidence['activity'])
        paths = workspace.operation_paths(target, task_id)
        info, operation = target / '.info', paths['operation_dir']
        owned = _owned_names(target, task_id)
        created = {}
        def claim(path, *, directory=False, flexible=False):
            if directory:
                path.mkdir()
            else:
                with path.open('xb'):
                    pass
            created[path] = (_created_identity(path, flexible), flexible)
        def change(path, operation):
            identity, flexible = created[path]
            if _created_identity(path, flexible) != identity:
                refuse('CLEANUP_UNKNOWN', 'Metadata changed before an owned write; preserve it.')
            operation()
            created[path] = (_created_identity(path, flexible), flexible)
        committed = False
        try:
            claim(info, directory=True)
            claim(info / 'workspace.lock', flexible=True)
            with workspace._lock(target):
                _validate(*args, ignored={'.info', '.info/workspace.lock'})
                route = prepare_adoption_route(task_id, run_archive.PROJECT_ROOT / 'audit', dict(
                    project=project, business_task_id=business, workspace_id=business,
                    run_dir=str(target), operation_dir=str(operation)))
                for directory in _operation_directories(info, operation):
                    claim(directory, directory=True)
                for key in ('state', 'result', 'report'):
                    claim(paths[key])
                binding = dict(workspace_root=str(target), workspace_id=business, project=project,
                    business_task_id=business, run_state='.info/run_state.json', allocation_receipt=task_id)
                receipt = make_api_receipt(API_ID, VERSION, 'SUCCESS', started, input_data={'params': params},
                    output={'task_id': task_id, 'binding': binding, 'preserved_files': evidence['manifest']['file_count']},
                    artifacts=[str(paths['report'])])
                state = dict(wf_id=business, project=project, business_task_id=business, asset_name=business,
                    workspace_mode=True, created_at=time.strftime('%Y-%m-%d %H:%M:%S'), scene_hosts={})
                record = dict(task_id=task_id, workspace_id=business, business_task_id=business,
                    project=project, kind='api', api_id=API_ID, status='SUCCESS', submitted_at=time.time(),
                    run_dir=str(target), operation_dir=str(operation), report_path=str(paths['report']))
                change(paths['state'], lambda: _durable(paths['state'], record))
                change(paths['result'], lambda: _durable(paths['result'], receipt))
                from cgi_pipeline.core.task_report_writer import init_report, finalize_report
                change(paths['report'], lambda: init_report(paths['report'], record))
                change(paths['report'], lambda: finalize_report(paths['report'], record, 'SUCCESS'))
                with paths['report'].open('r+b') as stream:
                    os.fsync(stream.fileno())
                claim(target / 'REPORT.md')
                claim(target / 'REPORT.md.tmp')
                change(target / 'REPORT.md', lambda: workspace._render_summary(target, state))
                del created[target / 'REPORT.md.tmp']  # The summary owner moved this inode to REPORT.md.
                with (target / 'REPORT.md').open('r+b') as stream:
                    os.fsync(stream.fileno())
                state['adoption'] = dict(task_id=task_id, route=route,
                    manifest_sha256=params['manifest_sha256'], maintenance_root=str(maintenance),
                    maintenance_workspace_id=maintenance_id, maintenance_business_task_id=maintenance_business,
                    files={name: digest_file(paths[key]) for key, name in
                           (('state', 'state.json'), ('result', 'result.json'), ('report', 'REPORT.md'))})
                if checkpoint:
                    checkpoint('prepared', target, route)
                _check_created(created)
                _validate(*args, ignored=owned)
                _fresh(project, business, target, evidence['activity'])
                validate_inventory(evidence['manifest'], target, project, business, ignored=owned)
                check_ttl(evidence['activity'])
                _check_created(created)
                _publish_identity(state_path, state)
                committed = True
                if checkpoint:
                    checkpoint('committed', target, route)
                return receipt
        finally:
            # Unknown publication outcome retains all evidence for the original ID.
            if not committed and not os.path.lexists(state_path):
                lock_path = info / 'workspace.lock'
                if lock_path in created:
                    try:
                        local_stat(lock_path)
                    except FileNotFoundError:
                        # Windows FileLock removes its lock on release, after our with block.
                        del created[lock_path]
                _cleanup_created(created)
