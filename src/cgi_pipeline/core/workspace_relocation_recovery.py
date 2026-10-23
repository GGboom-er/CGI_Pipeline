"""Original-ID rollback of interrupted relocation; never replays a move."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cgi_pipeline.core import run_archive, workspace
from cgi_pipeline.core.workspace_adoption_evidence import file_bytes, inventory, read_sealed, local_chain, digest_file
from cgi_pipeline.core.workspace_relocation_evidence import (
    refuse, digest, _root, _durable, _exclusive, _identity, LOCKS, _activity, check_approved_activity_ttl,
)


def verify_rollback(maintenance, proof, journal, *, require_current_source=True):
    """A mutable phase alone can never release the old submission fence."""
    token = read_sealed(maintenance, f".info/{proof['task_id']}_relocation_rollback.json",
                        journal.get('rollback_witness_sha256', ''))
    if (token.get('kind') != 'cgi_workspace_relocation_rollback' or token.get('task_id') != proof['task_id']
            or token.get('proof_sha256') != digest(proof)):
        refuse('RECOVERY_INVALID', 'No matching durable rollback witness exists.')
    if (Path(proof['target_root']) / proof['commit_relpath']).exists():
        refuse('RECOVERY_INVALID', 'A commit witness conflicts with the claimed rollback.')
    if require_current_source:
        old = proof['old_binding']
        _, state = workspace._validate_run_identity(proof['source_root'], old['workspace_id'], old['project'], old['business_task_id'])
        if state.get('relocation', {}).get('task_id') == proof['task_id']:
            refuse('RECOVERY_INVALID', 'The original native identity was not restored.')
    return token


def rollback_prepared(maintenance, proof, journal):
    """Prove every member and identity before reversing any rename."""
    from cgi_pipeline.core.workspace_relocation import API_ID
    task_id = proof['task_id']
    source = _root(proof['old_binding']['project'], proof['binding']['business_task_id'], exists=False)
    target = _root(proof['binding']['project'], proof['binding']['business_task_id'], exists=False)
    if source == target or proof['source_root'] != str(source) or proof['target_root'] != str(target):
        refuse('RECOVERY_INVALID', 'Recovery scope is not canonical.')
    if (target / proof['commit_relpath']).exists():
        refuse('RECOVERY_INVALID', 'A published commit must be recovered, never rolled back.')
    paths = workspace.operation_paths(maintenance, task_id)
    for key in ('state', 'result'):
        if digest_file(paths[key]) != journal['native_files'][key]:
            refuse('RECOVERY_INVALID', 'Prepared original operation bytes changed; preserve both roots.')
    receipt = workspace._read(paths['result'])
    if receipt.get('api_id') != API_ID or receipt.get('output', {}).get('binding') != proof['binding']:
        refuse('RECOVERY_INVALID', 'Prepared operation owner differs.')
    params = receipt['input']['params']
    if params.get('manifest_sha256') != proof['approved_manifest_sha256']:
        refuse('RECOVERY_INVALID', 'Prepared operation no longer identifies the approved scope.')
    approved = read_sealed(maintenance, params['manifest_relpath'], params['manifest_sha256'])
    if (approved.get('source_root') != str(source) or approved.get('target_root') != str(target)
            or approved.get('business_task_id') != proof['binding']['business_task_id']):
        refuse('RECOVERY_INVALID', 'Approved recovery scope differs from the original roots.')
    original = base64.b64decode(journal['original_identity_base64'], validate=True)
    if hashlib.sha256(original).hexdigest() != approved['source_identity_sha256']:
        refuse('RECOVERY_INVALID', 'Original identity backup differs from the approved scope.')
    import json
    old_state = json.loads(original)
    source_rows = inventory(source, ignored=LOCKS) if source.exists() else []
    target_rows = inventory(target, ignored=LOCKS) if target.exists() else []
    old_members = set(journal['members'])
    def native_identity(rows, root):
        filtered = []
        for row in rows:
            if row['path'] == '.info/run_state.json':
                state = workspace._read(root / row['path'])
                if file_bytes(root / row['path']) != original and digest(state) != proof['prepared_state_sha256']:
                    refuse('RECOVERY_UNKNOWN', 'Identity has foreign changes; preserve both roots.')
                filtered.append(next(r for r in approved['source_entries'] if r['path'] == row['path']))
            else:
                filtered.append(row)
        return filtered
    source_rows = native_identity(source_rows, source)
    target_rows = native_identity(target_rows, target)
    if journal['target_existed']:
        moved_rows = [row for row in target_rows if row['path'].split('/')[0] in old_members]
        original_target = [row for row in target_rows if row['path'].split('/')[0] not in old_members]
        if original_target != approved['target_entries']:
            refuse('RECOVERY_UNKNOWN', 'Original target members changed; preserve both roots.')
        combined = sorted(source_rows + moved_rows, key=lambda row: row['path'])
        if combined != approved['source_entries']:
            refuse('RECOVERY_UNKNOWN', 'Original source members cannot be reconstructed exactly.')
        moves = []
        for name in journal['members']:
            at_source, at_target = (source / name).exists(), (target / name).exists()
            if at_source == at_target:
                refuse('RECOVERY_UNKNOWN', 'A source member has ambiguous or missing ownership.')
            if at_target:
                moves.append(name)
    else:
        if bool(source_rows) == bool(target_rows):
            refuse('RECOVERY_UNKNOWN', 'Whole-root rename has ambiguous or missing ownership.')
        if (source_rows or target_rows) != approved['source_entries']:
            refuse('RECOVERY_UNKNOWN', 'Whole-root contents changed after interruption.')
        moves = ['.'] if target_rows else []
    journal_path = maintenance / '.info' / f'{task_id}_relocation_journal.json'
    journal['phase'] = 'ROLLING_BACK'
    _durable(journal_path, journal)
    with run_archive.allocation_lock():
        if moves == ['.']:
            target.rename(source)
        elif moves:
            if not source.exists():
                local_chain(source.parent)
                source.mkdir()
            for name in reversed(moves):
                (target / name).rename(source / name)
    # The validated backup is the only intentionally replaced original member.
    identity = source / '.info/run_state.json'
    with identity.open('wb') as stream:
        stream.write(original)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    if inventory(source, ignored=LOCKS) != approved['source_entries']:
        refuse('RECOVERY_UNKNOWN', 'Restored source verification failed; preserve all evidence.')
    if journal['target_existed'] and inventory(target, ignored=LOCKS) != approved['target_entries']:
        refuse('RECOVERY_UNKNOWN', 'Restored destination verification failed; preserve all evidence.')
    token = dict(kind='cgi_workspace_relocation_rollback', task_id=task_id, proof_sha256=digest(proof),
        restored_source_sha256=digest(approved['source_entries']), restored_target_sha256=digest(approved['target_entries']),
        restored_identity_sha256=approved['source_identity_sha256'])
    token_path = maintenance / '.info' / f'{task_id}_relocation_rollback.json'
    if token_path.exists():
        read_sealed(maintenance, token_path.relative_to(maintenance).as_posix(), digest(token))
    else:
        _exclusive(token_path, token)
    journal.update(phase='ROLLED_BACK', moving=None, rollback_witness_sha256=digest(token))
    _durable(journal_path, journal)
    return {'original_task_id': task_id, 'rolled_back': True, 'source_root': str(source),
            'target_root': str(target), 'replayed': False}


def reconcile_workspace(params, context, maintenance):
    """Explicit same-owner reconciliation; refreshed activity is mandatory."""
    from contextlib import ExitStack
    from cgi_pipeline.contracts import make_api_receipt
    from cgi_pipeline.core.workspace_relocation import API_ID, VERSION, verify_relocation_commit, _foreground_clear
    from cgi_pipeline.core.workspace_relocation_evidence import workspace_fence
    from cgi_pipeline.core.workspace_relocation_foreground import validate_confirmation
    import time
    started = time.perf_counter()
    task_id = params.get('original_task_id', '')
    workspace._validate_id(task_id)
    journal_path = maintenance / '.info' / f'{task_id}_relocation_journal.json'
    journal = workspace._read(journal_path)
    proof_relpath = f'.info/{task_id}_relocation.json'
    proof = read_sealed(maintenance, proof_relpath, journal['proof_sha256'])
    if (proof.get('task_id') != task_id or proof.get('maintenance_root') != str(maintenance)
            or proof['maintenance_business_task_id'] != context.extras.get('business_task_id')):
        refuse('RECOVERY_INVALID', 'The original maintenance owner must perform reconciliation.')
    project, target_project = proof['old_binding']['project'], proof['binding']['project']
    business = proof['binding']['business_task_id']
    if (params['source_project'], params['target_project'], params['business_task_id']) != (project, target_project, business):
        refuse('RECOVERY_INVALID', 'Reconciliation cannot change the original operation scope.')
    source = _root(project, business, exists=False)
    target = _root(target_project, business, exists=False)
    with ExitStack() as stack:
        for namespace in sorted((project, target_project)):
            stack.enter_context(workspace_fence(namespace, business))
        if (target / proof['commit_relpath']).exists():
            committed = verify_relocation_commit(maintenance, task_id, proof_relpath, journal['proof_sha256'])
            if committed['report_view_status'] == 'PENDING':
                with workspace._lock(target):
                    workspace._render_summary(target, workspace._read(target / '.info/run_state.json'))
                committed = verify_relocation_commit(maintenance, task_id, proof_relpath, journal['proof_sha256'])
            journal = workspace._read(journal_path)
            journal['phase'] = 'COMMITTED'
            _durable(journal_path, journal)
            return make_api_receipt(API_ID, VERSION, 'SUCCESS', started,
                output={'original_task_id': task_id, 'committed': True, 'binding': committed['binding'],
                        'report_view_status': committed['report_view_status'], 'replayed': False})
        if journal.get('phase') == 'ROLLED_BACK':
            verify_rollback(maintenance, proof, journal, require_current_source=False)
            return make_api_receipt(API_ID, VERSION, 'SUCCESS', started,
                output={'original_task_id': task_id, 'rolled_back': True, 'replayed': False})
        for root, namespace, prefix in ((source, project, ''), (target, target_project, 'target_')):
            activity = read_sealed(maintenance, params[prefix + 'activity_relpath'], params[prefix + 'activity_sha256'])
            _activity(root, namespace, business, activity)
        original = workspace._read(workspace.operation_paths(maintenance, task_id)['result'])
        original_params = original.get('input', {}).get('params', {})
        if original_params.get('manifest_sha256') != proof['approved_manifest_sha256']:
            refuse('RECOVERY_INVALID', 'Recovery must use the original sealed foreground scope.')
        approved = read_sealed(maintenance, original_params['manifest_relpath'], proof['approved_manifest_sha256'])
        if 'foreground_confirmation' in approved and not isinstance(approved['foreground_confirmation'], dict):
            refuse('FOREGROUND_UNKNOWN', 'A supplied foreground confirmation must be an object.')
        confirmation = validate_confirmation(approved.get('foreground_confirmation'), (source, target), maintenance,
                                             allow_expired=True)
        _foreground_clear((source, target), context.extras['task_id'], proof['business_project'],
                          confirmation, maintenance)
        check_approved_activity_ttl(maintenance, params, both=True)
        output = rollback_prepared(maintenance, proof, journal)
        return make_api_receipt(API_ID, VERSION, 'SUCCESS', started, output=output)
