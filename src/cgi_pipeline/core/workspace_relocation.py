"""Sealed workspace relocation. Historical receipts are never rewritten.

The target commit witness is the publication point. A lost reply is recovered
from that witness using the original server operation ID, never by resubmission.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from contextlib import ExitStack
from pathlib import Path

from cgi_pipeline.contracts import make_api_receipt, validate_receipt
from cgi_pipeline.core import run_archive, workspace
from cgi_pipeline.core.workspace_adoption_evidence import (
    digest_file, file_bytes, inventory, local_chain, local_stat, read_sealed,
)

API_ID, VERSION = 'pipeline.workspace.relocate', '1.1.0'
PATH_FIELDS = ('run_dir', 'operation_dir', 'report_path', 'workspace_report_path', 'info_dir', 'manifest_path')


from cgi_pipeline.core.workspace_relocation_evidence import (
    refuse, digest, _durable, _exclusive, _root, workspace_fence, _route_path,
    _identity, _binding, _scope, _foreground_clear, LOCKS,
    check_approved_activity_ttl,
)
from cgi_pipeline.core.workspace_relocation_foreground import validate_confirmation


def _native_records(maintenance, task_id, receipt):
    paths = workspace.operation_paths(maintenance, task_id)
    paths['operation_dir'].mkdir(parents=True, exist_ok=True)
    state = dict(task_id=task_id, workspace_id=maintenance.name, business_task_id=maintenance.name,
        project=maintenance.parent.name, workspace_project=maintenance.parent.name,
        run_dir=str(maintenance), operation_dir=str(paths['operation_dir']), report_path=str(paths['report']),
        kind='api', api_id=API_ID, status='SUCCESS', submitted_at=time.time())
    _exclusive(paths['state'], state)
    _exclusive(paths['result'], receipt)
    with paths['report'].open('x', encoding='utf-8') as stream:
        stream.write(f'# Workspace relocation `{task_id}`\n\nCommit recovery is verified against the target witness.\n')
        stream.flush()
        os.fsync(stream.fileno())
    return paths


def relocate_workspace(params, context, *, checkpoint=None):
    started = time.perf_counter()
    task_id = context.extras.get('task_id', '')
    workspace._validate_id(task_id)
    maintenance_id = context.extras.get('workspace_id', '')
    maintenance_business = context.extras.get('business_task_id', '')
    maintenance = workspace._resolve(maintenance_id, workspace.workspace_project(
        {'project': context.project, **context.extras}), maintenance_business)
    workspace.verify_workspace_identity(maintenance, maintenance_id, maintenance.parent.name, maintenance_business)
    if maintenance.name != maintenance_id or maintenance_business != maintenance_id:
        refuse('IDENTITY_INVALID', 'Relocation requires a canonical stable maintenance task workspace.')
    if params['mode'] == 'reconcile':
        from cgi_pipeline.core.workspace_relocation_recovery import reconcile_workspace
        return reconcile_workspace(params, context, maintenance)
    ids = sorted({(params['source_project'], params['business_task_id']),
                  (params['target_project'], params['business_task_id'])})
    with ExitStack() as stack:
        for project, business in ids:
            stack.enter_context(workspace_fence(project, business))
        source, target, old_state, approved, members, target_rows, activity = _scope(maintenance, params)
        foreground = _foreground_clear((source, target), task_id, approved.get('business_project', context.project),
                                       approved.get('foreground_confirmation'), maintenance)
        summary = dict(source_root=str(source), target_root=str(target), members=members,
                       target_policy=params['target_policy'], source_files=sum(r['kind'] == 'file' for r in approved['source_entries']))
        if params['mode'] == 'preview':
            return make_api_receipt(API_ID, VERSION, 'SUCCESS', started, input_data={'params': params},
                                    output={'preview': True, 'plan_sha256': params['manifest_sha256'],
                                            'foreground_evidence': foreground, **summary})
        if params['mode'] != 'apply':
            refuse('MODE_INVALID', 'Use preview or apply.')
        route_path = _route_path(params['source_project'], params['business_task_id'])
        previous_route = None
        if route_path.exists():
            # A proven rollback permits a new explicit operation. The old audit
            # route and proofs remain immutable and recoverable by their ID.
            if relocation_for_root(source) is not None:
                refuse('CONFLICT', 'A committed relocation route already owns this root.')
            previous_route = file_bytes(route_path)
        proof_path = maintenance / '.info' / f'{task_id}_relocation.json'
        journal_path = maintenance / '.info' / f'{task_id}_relocation_journal.json'
        commit_relative = f'.info/{task_id}_relocation_commit.json'
        new_state = dict(old_state)
        new_state['project'] = params['target_project']
        new_state['relocation'] = dict(task_id=task_id, maintenance_root=str(maintenance),
            maintenance_business_task_id=maintenance_business, manifest_relpath=proof_path.relative_to(maintenance).as_posix(),
            commit_relpath=commit_relative, source_root=str(source))
        report_backup = None
        if (source / 'REPORT.md').exists():
            original_report = file_bytes(source / 'REPORT.md', limit=16 * 1024 * 1024)
            backup_path = maintenance / '.info' / f'{task_id}_original_REPORT.md'
            with backup_path.open('xb') as stream:
                stream.write(original_report)
                stream.flush()
                os.fsync(stream.fileno())
            report_backup = dict(relpath=backup_path.relative_to(maintenance).as_posix(), sha256=hashlib.sha256(original_report).hexdigest())
        binding = _binding(target, new_state, task_id)
        old_binding = _binding(source, old_state, approved.get('original_allocation_receipt', ''))
        proof = dict(schema_version=1, kind='cgi_workspace_relocation_commit', task_id=task_id, api_id=API_ID,
            maintenance_root=str(maintenance), maintenance_business_task_id=maintenance_business,
            old_binding=old_binding, binding=binding, source_root=str(source), target_root=str(target),
            commit_relpath=commit_relative, identity_sha256=digest(_identity(new_state)),
            prepared_state_sha256=digest(new_state),
            original_report_backup=report_backup,
            foreground_evidence=foreground,
            approved_manifest_sha256=params['manifest_sha256'], original_allocation_receipt=old_binding['allocation_receipt'],
            business_project=approved.get('business_project', context.project), **{'preservation': summary})
        proof['preservation']['view_rebuild_plan'] = ['REPORT.md'] if report_backup else []
        proof_sha = digest(proof)
        output = {**proof, 'relocation_task_id': task_id,
                  'receipt_manifest_relpath': proof_path.relative_to(maintenance).as_posix(),
                  'receipt_manifest_sha256': proof_sha, 'commit_witness_sha256': proof_sha}
        receipt = make_api_receipt(API_ID, VERSION, 'SUCCESS', started, input_data={'params': params}, output=output)
        _exclusive(proof_path, proof)
        paths = _native_records(maintenance, task_id, receipt)
        journal = dict(task_id=task_id, phase='PREPARED', source_root=str(source), target_root=str(target),
            proof_sha256=proof_sha, original_identity_base64=base64.b64encode(file_bytes(source / '.info/run_state.json')).decode(),
            original_identity_sha256=approved['source_identity_sha256'], members=members, moved=[], target_existed=target_rows is not None,
            native_files={k: digest_file(paths[k]) for k in ('state', 'result')})
        _exclusive(journal_path, journal)
        route = dict(kind='workspace_relocation_route', task_id=task_id, maintenance_root=str(maintenance),
                     manifest_relpath=proof_path.relative_to(maintenance).as_posix(), manifest_sha256=proof_sha)
        from cgi_pipeline.core.task_audit import prepare_relocation_route
        prepare_relocation_route(task_id, run_archive.PROJECT_ROOT / 'audit', route)
        if previous_route is None:
            _exclusive(route_path, route)
        else:
            if file_bytes(route_path) != previous_route:
                refuse('CONFLICT', 'Relocation route changed before compare-and-swap.')
            _durable(route_path, route)
        committed = False
        try:
            if checkpoint:
                checkpoint('prepared', source, target)
            # Rehash after all potentially slow preparation, then refresh the
            # broker/process attestation immediately before any rename.
            _scope(maintenance, params)
            foreground_recheck = _foreground_clear((source, target), task_id,
                approved.get('business_project', context.project), approved.get('foreground_confirmation'), maintenance)
            journal['foreground_pre_rename_evidence'] = foreground_recheck
            _durable(journal_path, journal)
            with run_archive.allocation_lock():
                check_approved_activity_ttl(maintenance, params)
                validate_confirmation(approved.get('foreground_confirmation'), (source, target), maintenance)
                if target_rows is None:
                    journal.update(phase='RENAMING', moving='.')
                    _durable(journal_path, journal)
                    source.rename(target)
                    journal['moved'] = ['.']
                else:
                    for name in members:
                        journal.update(phase='RENAMING', moving=name)
                        _durable(journal_path, journal)
                        if (target / name).exists():
                            refuse('CONFLICT', 'A late target member collision prevents relocation.')
                        (source / name).rename(target / name)
                        journal['moved'].append(name)
                        if checkpoint:
                            checkpoint('member_moved', source, target)
            journal.update(phase='MOVED', moving=None)
            _durable(journal_path, journal)
            if checkpoint:
                checkpoint('moved', source, target)
            expected = [r for r in approved['source_entries'] if r['path'] != '.info/run_state.json']
            expected += target_rows or []
            observed = inventory(target, ignored=LOCKS | {'.info/run_state.json'})
            if observed != sorted(expected, key=lambda row: row['path']):
                refuse('DRIFT', 'Destination members differ; preserving both sides for rollback.')
            with run_archive.allocation_lock():
                check_approved_activity_ttl(maintenance, params)
                validate_confirmation(approved.get('foreground_confirmation'), (source, target), maintenance)
                if target_rows is not None:
                    source.rmdir()  # Empty only, before commit; never recursive.
                _durable(target / '.info/run_state.json', new_state)
                if checkpoint:
                    checkpoint('identity_written', source, target)
                # A no-replace hardlink publishes exactly the sealed bytes; no
                # replace-rename can silently overwrite a foreign commit witness.
                os.link(proof_path, target / commit_relative, follow_symlinks=False)
                committed = True
            if checkpoint:
                checkpoint('committed', source, target)
            journal.update(phase='COMMITTED', moving=None)
            _durable(journal_path, journal)
            if report_backup:
                from cgi_pipeline.core.workspace_relocation_view import current_path
                with workspace._lock(target):
                    workspace._render_summary(target, new_state, path_projection=lambda value: current_path(value, proof))
            return receipt
        except BaseException:
            # A lost kernel return may follow an actual commit. Never undo a
            # witness that already published, and never replay this operation.
            witness = target / commit_relative
            if committed or witness.exists():
                raise
            try:
                from cgi_pipeline.core.workspace_relocation_recovery import rollback_prepared
                rollback_prepared(maintenance, proof, journal)
            except BaseException as rollback:
                refuse('ROLLBACK_UNKNOWN', 'Rollback requires original-ID investigation: ' + str(rollback))
            raise


def verify_relocation_commit(maintenance_root, relocation_task_id, receipt_manifest_relpath, receipt_manifest_sha256):
    """Read-only verification shared with Notes; no caller receipt is trusted."""
    maintenance = local_chain(Path(maintenance_root).absolute())
    workspace._validate_id(relocation_task_id)
    proof = read_sealed(maintenance, receipt_manifest_relpath, receipt_manifest_sha256)
    if (proof.get('schema_version') != 1 or proof.get('kind') != 'cgi_workspace_relocation_commit'
            or proof.get('api_id') != API_ID or proof.get('task_id') != relocation_task_id
            or proof.get('maintenance_root') != str(maintenance)):
        refuse('RECOVERY_INVALID', 'Original relocation manifest identity differs.')
    if (receipt_manifest_relpath != f'.info/{relocation_task_id}_relocation.json'
            or proof.get('commit_relpath') != f'.info/{relocation_task_id}_relocation_commit.json'):
        refuse('RECOVERY_INVALID', 'Relocation witness paths differ from their canonical operation identity.')
    source_binding, binding = proof['old_binding'], proof['binding']
    source = _root(source_binding['project'], binding['business_task_id'], exists=False)
    target = _root(binding['project'], binding['business_task_id'])
    if proof['source_root'] != str(source) or proof['target_root'] != str(target):
        refuse('RECOVERY_INVALID', 'Relocation roots are not canonical.')
    witness = target / proof['commit_relpath']
    local_chain(witness)
    if not witness.is_relative_to(target) or digest_file(witness) != receipt_manifest_sha256:
        refuse('RECOVERY_INVALID', 'No matching target commit witness exists.')
    _, native = workspace._validate_run_identity(target, binding['workspace_id'], binding['project'], binding['business_task_id'])
    if digest(_identity(native)) != proof['identity_sha256']:
        refuse('RECOVERY_INVALID', 'Committed native identity changed.')
    backup = proof.get('original_report_backup')
    if backup:
        read_path = maintenance / backup['relpath']
        local_chain(read_path)
        if (not read_path.is_relative_to(maintenance) or digest_file(read_path) != backup['sha256']):
            refuse('RECOVERY_INVALID', 'Original root report backup changed.')
    paths = workspace.operation_paths(maintenance, relocation_task_id)
    journal = workspace._read(maintenance / '.info' / f'{relocation_task_id}_relocation_journal.json')
    if journal.get('phase') == 'ROLLED_BACK' or journal.get('proof_sha256') != receipt_manifest_sha256:
        refuse('RECOVERY_INVALID', 'Relocation journal has no matching commit.')
    for key in ('state', 'result'):
        if digest_file(paths[key]) != journal['native_files'][key]:
            refuse('RECOVERY_INVALID', 'Prepared native operation record changed.')
    state, receipt = workspace._read(paths['state']), workspace._read(paths['result'])
    receipt_version = receipt.get('api_version')
    if receipt_version not in ('1.0.0', VERSION):
        refuse('RECOVERY_INVALID', 'Original relocation receipt has an unsupported API version.')
    validate_receipt(receipt, api_id=API_ID, api_version=receipt_version)
    if (state.get('task_id') != relocation_task_id or state.get('status') != 'SUCCESS'
            or state.get('api_id') != API_ID or receipt.get('status') != 'SUCCESS'
            or receipt.get('output', {}).get('binding') != binding
            or receipt.get('output', {}).get('receipt_manifest_sha256') != receipt_manifest_sha256):
        refuse('RECOVERY_INVALID', 'Original operation did not commit this binding.')
    workspace.verify_workspace_identity(maintenance, maintenance.name, maintenance.parent.name, proof['maintenance_business_task_id'])
    from cgi_pipeline.core.workspace_relocation_view import report_view_status
    return {**proof, 'relocation_task_id': relocation_task_id, 'commit_witness_sha256': receipt_manifest_sha256,
            'report_view_status': report_view_status(proof, journal),
            'receipt_manifest_relpath': receipt_manifest_relpath, 'receipt_manifest_sha256': receipt_manifest_sha256,
            'receipt': receipt}


def recover_relocation(task_id, route):
    try:
        if route.get('kind') != 'workspace_relocation_route' or route.get('task_id') != task_id:
            refuse('RECOVERY_INVALID', 'Relocation route identity differs.')
        journal = workspace._read(Path(route['maintenance_root']) / '.info' / f'{task_id}_relocation_journal.json')
        if journal.get('phase') == 'ROLLED_BACK' and journal.get('proof_sha256') == route['manifest_sha256']:
            proof = read_sealed(route['maintenance_root'], route['manifest_relpath'], route['manifest_sha256'])
            from cgi_pipeline.core.workspace_relocation_recovery import verify_rollback
            verify_rollback(Path(route['maintenance_root']), proof, journal, require_current_source=False)
            return dict(task_id=task_id, status='BLOCKED', terminal=True, api_id=API_ID,
                        rolled_back=True, replayed=False, recovery_source='native_relocation_rollback')
        result = verify_relocation_commit(route['maintenance_root'], task_id, route['manifest_relpath'], route['manifest_sha256'])
        return dict(task_id=task_id, status='SUCCESS', terminal=True, api_id=API_ID,
                    receipt=result['receipt'], **result['binding'], run_dir=result['target_root'],
                    report_view_status=result['report_view_status'],
                    recovery_source='native_relocation_commit')
    except (OSError, ValueError, KeyError, TypeError, workspace.WorkspaceError):
        return dict(task_id=task_id, status='NEEDS_ATTENTION', terminal=False,
            error_code='WORKSPACE_RELOCATION_COMMIT_UNKNOWN', error='No verified relocation commit; preserve both roots and recover this ID, never replay.',
            recovery_hint='Use pipeline.workspace.relocate mode=reconcile, original_task_id=' + task_id +
                ' from the original maintenance workspace with fresh source and target activity attestations; this only rolls back uncommitted members.')


def relocation_for_root(root):
    """Resolve a committed historical root without scanning or changing files."""
    root = Path(root).absolute()
    base = (run_archive.PROJECT_ROOT / 'projects').absolute()
    if not root.is_relative_to(base) or len(root.relative_to(base).parts) != 2:
        return None
    project, business = root.relative_to(base).parts
    path = _route_path(project, business)
    if not path.exists():
        return None
    local_chain(path)
    route = workspace._read(path)
    journal = workspace._read(Path(route['maintenance_root']) / '.info' / f"{route['task_id']}_relocation_journal.json")
    if journal.get('phase') == 'ROLLED_BACK':
        proof = read_sealed(route['maintenance_root'], route['manifest_relpath'], route['manifest_sha256'])
        from cgi_pipeline.core.workspace_relocation_recovery import verify_rollback
        verify_rollback(Path(route['maintenance_root']), proof, journal)
        return None
    return verify_relocation_commit(route['maintenance_root'], route['task_id'], route['manifest_relpath'], route['manifest_sha256'])


def assert_submission_location(project, business):
    proof = relocation_for_root(run_archive.PROJECT_ROOT / 'projects' / project / business)
    if proof:
        refuse('MOVED', 'Use the committed storage namespace ' + proof['binding']['project'] + '; the old root cannot be recreated.')


def project_recovery(result):
    """Map only effective location fields, preserving original receipt content."""
    if not result.get('run_dir'):
        return result
    try:
        proof = relocation_for_root(result['run_dir'])
        if proof is None:
            state_path = Path(result['run_dir']) / '.info/run_state.json'
            if state_path.exists():
                native = workspace._read(state_path)
                relocation = native.get('relocation') or {}
                if relocation:
                    proof = verify_relocation_commit(relocation['maintenance_root'], relocation['task_id'],
                        relocation['manifest_relpath'], digest_file(Path(result['run_dir']) / relocation['commit_relpath']))
    except (OSError, ValueError, KeyError, workspace.WorkspaceError) as exc:
        # Location uncertainty never changes the original operation terminal.
        return {**result, 'location_status': 'RELOCATION_UNKNOWN',
                'location_error_code': getattr(exc, 'code', 'WORKSPACE_RELOCATION_COMMIT_UNKNOWN')}
    if proof is None:
        return result
    from cgi_pipeline.core.workspace_relocation_view import attach_effective_locations
    return attach_effective_locations(result, proof)


def recover_relocated_execution(payload):
    """A late-ACK old payload may read a terminal receipt but never execute again."""
    if not payload.get('run_dir'):
        return None
    proof = relocation_for_root(payload['run_dir'])
    if proof is None:
        return None
    if (payload.get('workspace_id') != proof['binding']['workspace_id']
            or payload.get('business_task_id') != proof['binding']['business_task_id']):
        refuse('RECOVERY_INVALID', 'Old execution owner differs from the relocated workspace.')
    paths = workspace.operation_paths(proof['target_root'], payload['task_id'])
    from cgi_pipeline.core.task_status import TERMINAL_STATUSES
    result = workspace._read(paths['result'])
    if result.get('task_id') != payload['task_id'] or result.get('status') not in TERMINAL_STATUSES:
        refuse('RECOVERY_INVALID', 'The old operation has no terminal native receipt; never replay.')
    return project_recovery(result)
