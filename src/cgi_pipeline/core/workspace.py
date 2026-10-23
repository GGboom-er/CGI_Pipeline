"""One business workspace, with independently recoverable operation records."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from filelock import FileLock

from cgi_pipeline.contracts import ApiContractError
from cgi_pipeline.core import run_archive


class WorkspaceError(ApiContractError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def workspace_project(payload: dict) -> str:
    """Storage identity; project continues to select business configuration."""
    value = payload.get('workspace_project')
    if value is None:
        value = payload.get('project') or 'default'
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', value):
        raise WorkspaceError('WORKSPACE_PROJECT_INVALID', 'Invalid workspace project storage namespace.')
    return value


def _read(path: Path) -> dict:
    if path.is_symlink():
        raise WorkspaceError('WORKSPACE_PATH_INVALID', f'Workspace state cannot be a file link: {path}')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise WorkspaceError('WORKSPACE_STATE_INVALID', f'Invalid state: {path}')
    return value


def _write(path: Path, value: dict) -> None:
    temporary = path.with_name(
        f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp'
    )
    if path.is_symlink() or temporary.is_symlink():
        raise WorkspaceError('WORKSPACE_PATH_INVALID', f'Workspace state cannot be a file link: {path}')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for attempt in range(4):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _lock(run_dir: Path) -> FileLock:
    path = run_dir / '.info' / 'workspace.lock'
    if path.is_symlink():
        raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Workspace lock cannot be a file link.')
    return FileLock(str(path), timeout=30)


def _validate_id(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise WorkspaceError('WORKSPACE_ID_INVALID', 'Use the workspace_id returned by a previous execution.')


def _resolved_path(path: Path) -> Path:
    """Compare resolved Windows aliases consistently during atomic replacement."""
    value = str(path.resolve())
    if os.name == 'nt' and re.match(r'^\\\\\?\\(?:[A-Za-z]:\\|UNC\\)', value):
        value = '\\\\' + value[8:] if value.startswith('\\\\?\\UNC\\') else value[4:]
    return Path(value)


def operation_paths(run_dir: str | Path, task_id: str) -> dict[str, Path]:
    """Resolve without writes, preserving existing flat or resident-compatible paths.
    Two layouts for one ID are ambiguous and must never silently fall back.
    """
    paths, _ = _operation_snapshot(Path(run_dir), task_id)
    return paths


def _operation_snapshot(run_dir: Path, task_id: str, *,
                        resolved_root: Path | None = None) -> tuple[dict[str, Path], dict | None]:
    """Validate all candidates and read identity once for a caller-owned snapshot.

    Only a single summary render supplies its already resolved root. Public
    operation_paths calls always perform a fresh validation; nothing is cached
    across calls or workspace transactions.
    """
    _validate_id(task_id)
    if resolved_root is None:
        resolved_root = _resolved_path(run_dir)
    info = run_dir / '.info'
    legacy = info / 'operations' / task_id
    flat = {'operation_dir': info, **{
        key: info / f'{task_id}_{name}' for key, name in
        (('state', 'state.json'), ('result', 'result.json'),
         ('report', 'REPORT.md'), ('manifest', 'manifest.json'))}}
    old = {'operation_dir': legacy, **{
        key: legacy / name for key, name in
        (('state', 'state.json'), ('result', 'result.json'),
         ('report', 'REPORT.md'), ('manifest', 'manifest.json'))}}
    candidates = dict.fromkeys((info, info / 'operations', legacy, *flat.values(), *old.values()))
    for path in candidates:
        if path.is_symlink() or not _resolved_path(path).is_relative_to(resolved_root):
            raise WorkspaceError('WORKSPACE_PATH_INVALID', f'Operation metadata must be local: {path}')
    has_flat = any(path.exists() for key, path in flat.items() if key != 'operation_dir')
    has_old = any(path.exists() for key, path in old.items() if key != 'operation_dir')
    if has_flat and has_old:
        raise WorkspaceError('WORKSPACE_OPERATION_CONFLICT', f'Two operation layouts exist for {task_id}.')
    selected = flat if has_flat else old
    state = _read(selected['state']) if selected['state'].exists() else None
    if state is not None and state.get('task_id') != task_id:
        raise WorkspaceError('WORKSPACE_STATE_INVALID', f'Operation state identity does not match {task_id}.')
    return selected, state


def _validate_workspace_location(workspace_root: str | Path, project: str) -> Path:
    """Validate the existing allocation layout without creating or discovering it."""
    candidate = Path(workspace_root)
    if not candidate.is_absolute():
        raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Workspace root must be an absolute path.')
    try:
        candidate = candidate.resolve()
        base = run_archive.PROJECT_ROOT.resolve()
        projects, runs = base / 'projects', base / 'runs'
        if candidate.is_relative_to(projects):
            parts = candidate.relative_to(projects).parts
            if len(parts) != 2 or parts[0] != run_archive._safe_segment(project, 'default'):
                raise WorkspaceError('WORKSPACE_BINDING_MISMATCH', 'Workspace directory/project does not match.')
        elif not (candidate.is_relative_to(runs) and len(candidate.relative_to(runs).parts) == 1):
            raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Workspace is outside the managed roots.')
        if not (candidate / '.info').resolve().is_relative_to(candidate):
            raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Workspace metadata must remain inside its directory.')
    except WorkspaceError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkspaceError('WORKSPACE_PATH_INVALID', str(exc)) from exc
    return candidate


def _validate_run_identity(workspace_root: str | Path, workspace_id: str,
                           project: str | None, business_task_id: str = '') -> tuple[Path, dict]:
    """One validation path shared by allocation, cache recovery and task recovery."""
    _validate_id(workspace_id)
    # Legacy callers omit project when retrieving an already allocated run.
    # Read its stored key, but an explicitly supplied key must always match.
    supplied = Path(workspace_root)
    if project is None:
        if not supplied.is_absolute():
            raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Workspace root must be an absolute path.')
        base = run_archive.PROJECT_ROOT.resolve()
        candidate = supplied.resolve()
        if not any(candidate.is_relative_to(base / name) for name in ('projects', 'runs')):
            raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Workspace is outside the managed roots.')
        if not (candidate / '.info').resolve().is_relative_to(candidate):
            raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Workspace metadata must remain inside its directory.')
        project = ''
        infer_project = True
    else:
        candidate = _validate_workspace_location(supplied, project)
        infer_project = False
    if not candidate.is_dir():
        raise WorkspaceError('WORKSPACE_NOT_FOUND', f'Workspace directory is missing: {candidate}')
    try:
        state = _read(candidate / '.info' / 'run_state.json')
    except WorkspaceError:
        raise
    except (OSError, ValueError) as exc:
        raise WorkspaceError('WORKSPACE_STATE_INVALID', f'Cannot read workspace identity: {exc}') from exc
    if infer_project:
        project = state.get('project')
        if not isinstance(project, str):
            raise WorkspaceError('WORKSPACE_STATE_INVALID', 'Workspace project must be a string.')
        candidate = _validate_workspace_location(candidate, project)
    if state.get('wf_id') != workspace_id or state.get('project') != project:
        raise WorkspaceError('WORKSPACE_BINDING_MISMATCH', 'Workspace identity/project does not match.')
    if business_task_id and state.get('business_task_id') != business_task_id:
        raise WorkspaceError('WORKSPACE_BINDING_MISMATCH', 'Workspace business task does not match.')
    return candidate, state


def verify_workspace_identity(workspace_root: str | Path, workspace_id: str,
                              project: str, business_task_id: str,
                              allocation_receipt: str = '') -> dict:
    """Read-only verification of native identity and an optional successful receipt.

    No Redis lookup, allocation, state mutation, runtime admission or DCC import
    occurs here. Mutable scene/last-activity fields never define task identity.
    """
    if not isinstance(project, str) or not project or any(char in project for char in '/\\:') or project in ('.', '..'):
        raise WorkspaceError('WORKSPACE_PROJECT_INVALID', 'Invalid project key.')
    _validate_id(business_task_id)
    if not isinstance(allocation_receipt, str):
        raise WorkspaceError('WORKSPACE_RECEIPT_INVALID', 'Allocation receipt must be an operation ID.')
    candidate, native = _validate_run_identity(workspace_root, workspace_id, project, business_task_id)
    relocation = native.get('relocation') or {}
    if allocation_receipt and allocation_receipt == relocation.get('task_id'):
        from cgi_pipeline.core.workspace_relocation import verify_relocation_commit, digest_file
        proof = candidate / relocation['commit_relpath']
        verified = verify_relocation_commit(relocation['maintenance_root'], allocation_receipt,
            relocation['manifest_relpath'], digest_file(proof))
        binding = verified['binding']
        if any(binding.get(k) != v for k, v in dict(workspace_root=str(candidate), workspace_id=workspace_id,
                project=project, business_task_id=business_task_id, allocation_receipt=allocation_receipt).items()):
            raise WorkspaceError('WORKSPACE_RECEIPT_INVALID', 'Relocation commit binding differs.')
        return {key: binding[key] for key in ('workspace_root', 'workspace_id', 'project',
                    'business_task_id', 'run_state', 'allocation_receipt')}
    if allocation_receipt:
        from cgi_pipeline.core.task_status import is_success
        _validate_id(allocation_receipt)
        paths = operation_paths(candidate, allocation_receipt)
        operation_dir = paths['operation_dir']
        if operation_dir.is_symlink() or not operation_dir.resolve().is_relative_to(candidate):
            raise WorkspaceError('WORKSPACE_RECEIPT_INVALID', 'Allocation receipt is outside its workspace.')
        try:
            receipt = _read(paths['state'])
        except (OSError, ValueError, WorkspaceError) as exc:
            raise WorkspaceError('WORKSPACE_RECEIPT_INVALID', f'Cannot read allocation receipt: {exc}') from exc
        expected = {'task_id': allocation_receipt, 'workspace_id': workspace_id,
                    'business_task_id': business_task_id}
        status = receipt.get('status')
        if (any(receipt.get(key) != value for key, value in expected.items())
                or not isinstance(status, str) or not is_success(status)):
            raise WorkspaceError('WORKSPACE_RECEIPT_INVALID', 'Allocation receipt identity/status does not match.')
        if ('project' in receipt or 'workspace_project' in receipt) and workspace_project(receipt) != project:
            raise WorkspaceError('WORKSPACE_RECEIPT_INVALID', 'Allocation receipt project does not match.')
        for key, expected_path in (('run_dir', candidate), ('operation_dir', operation_dir)):
            value = receipt.get(key)
            if not isinstance(value, str) or not value or Path(value).resolve() != expected_path.resolve():
                raise WorkspaceError('WORKSPACE_RECEIPT_INVALID', f'Allocation receipt {key} does not match.')
    return {'workspace_root': str(candidate), 'workspace_id': workspace_id,
            'project': project, 'business_task_id': business_task_id,
            'run_state': '.info/run_state.json', 'allocation_receipt': allocation_receipt}


def _resolve(workspace_id: str, project: str, business_task_id: str = '') -> Path:
    _validate_id(workspace_id)
    if not project or any(char in project for char in '/\\:') or project in ('.', '..'):
        raise WorkspaceError('WORKSPACE_PROJECT_INVALID', 'Invalid project key.')
    candidate = run_archive.get_run_dir(workspace_id)
    if not (candidate / '.info' / 'run_state.json').is_file():
        candidate = run_archive._find_existing_run_dir(workspace_id, project)
    if candidate is None:
        raise WorkspaceError('WORKSPACE_NOT_FOUND', 'Unknown workspace_id; retain its original project key.')
    return _validate_run_identity(candidate, workspace_id, project, business_task_id)[0]


def workspace_result(payload: dict) -> dict:
    if not payload.get('workspace_report_path'):
        return {'business_task_id': payload['business_task_id']} if payload.get('business_task_id') else {}
    return {key: payload[key] for key in (
        'workspace_id', 'business_task_id', 'run_dir', 'report_path',
        'workspace_report_path', 'operation_dir', 'runtime', 'project', 'workspace_project'
    ) if key in payload}


def _render_summary(run_dir: Path, state: dict, *, path_projection=None) -> None:
    if path_projection is None and state.get('relocation'):
        from cgi_pipeline.core.workspace_relocation_view import report_projection
        path_projection = report_projection(run_dir, state)
    info = run_dir / '.info'
    ids = {path.name[:-len('_state.json')] for path in info.glob('*_state.json')
           if path.name != 'run_state.json'}
    ids.update(path.parent.name for path in (info / 'operations').glob('*/state.json'))
    resolved_root = _resolved_path(run_dir)
    paths_by_id, records = {}, []
    for task_id in ids:
        paths, record = _operation_snapshot(run_dir, task_id, resolved_root=resolved_root)
        # Preserve the existing failure if an enumerated state disappears.
        if record is None:
            record = _read(paths['state'])
        paths_by_id[task_id] = paths
        records.append(record)
    records.sort(key=lambda row: (row['submitted_at'], row['task_id']))
    task_label = f" · Business task: `{state['business_task_id']}`" if state.get('business_task_id') else ''
    lines = [f"# {state.get('asset_name', 'untitled')} — Workspace Report", '',
             f"Workspace: `{state['wf_id']}` · Project: `{state['project']}`{task_label}", '',
             '| Operation | Action | Status | Report |', '|---|---|---|---|']
    for row in records:
        task_id = row['task_id']
        action = str(row.get('api_id') or row.get('workflow_id') or row['kind']).replace('|', '\\|')
        report = paths_by_id[task_id]['report'].relative_to(run_dir).as_posix()
        lines.append(f"| `{task_id}` | {action} | {row['status']} | [Details]({report}) |")
    if records:
        latest = records[-1]
        # Keep a fresh guard immediately before reading the latest report,
        # including unselected-layout links, conflicts and state identity.
        report = operation_paths(run_dir, latest['task_id'])['report']
        lines += ['', f"## Latest operation: `{latest['task_id']}`", '',
                  f"Status: **{latest['status']}**", '']
        if report.is_file():
            details = report.read_text(encoding='utf-8')
            details = re.sub(r'^\s*<!--\s*report:block:(?:start|end).*?-->\s*$', '', details, flags=re.MULTILINE)
            prefix = report.parent.relative_to(run_dir).as_posix()
            def rebase(match):
                target = match.group(2)
                if path_projection:
                    current = path_projection(target)
                    if current != target:
                        return match.group(1) + current + match.group(3)
                if (target.startswith(('#', '/')) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', target)):
                    return match.group(0)
                return match.group(1) + prefix + '/' + target + match.group(3)
            details = re.sub(r'(!?\[[^\]]*\]\()([^\s)]+)(\))', rebase, details)
            lines.append(details.rstrip())
        else:
            lines.append('Detailed report is pending publication.')
    temporary = run_dir / 'REPORT.md.tmp'
    if temporary.is_symlink() or (run_dir / 'REPORT.md').is_symlink():
        raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Workspace report cannot be a file link.')
    temporary.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    temporary.replace(run_dir / 'REPORT.md')
    if state.get('relocation'):
        from cgi_pipeline.core.workspace_relocation_view import record_report_view
        record_report_view(run_dir, state)


def refresh_report_for_path(report_path: str | Path) -> None:
    """Refresh the workspace view after publishing a registered task report.

    Call only after releasing the report lock; the workspace lock is outermost.
    Ordinary reports and auxiliary files have no workspace side effect.
    """
    path = Path(report_path)
    if path.parent.name == '.info' and path.name.endswith('_REPORT.md'):
        run_dir = path.parent.parent
        task_id = path.name[:-len('_REPORT.md')]
    elif path.name == 'REPORT.md' and path.parent.parent.name == 'operations' and path.parent.parent.parent.name == '.info':
        run_dir = path.parent.parent.parent.parent
        task_id = path.parent.name
    else:
        return
    if not (run_dir / '.info' / 'run_state.json').is_file():
        return
    with _lock(run_dir):
        paths = operation_paths(run_dir, task_id)
        if paths['report'] != path or not paths['state'].is_file():
            return
        _render_summary(run_dir, _read(run_dir / '.info' / 'run_state.json'))


def _operation_contracts(payload: dict, kind: str) -> list[tuple[dict, dict, str]]:
    """Resolve one complete call collection from one catalog generation."""
    if kind == 'workflow':
        return []
    steps = (payload.get('api_chain', []) if kind in ('batch', 'chain') else
             [{'api_id': payload.get('api_id'), 'parameters': payload.get('api_params', {})}])
    if not steps or not all(step.get('api_id') for step in steps):
        return []
    from cgi_pipeline.catalog import snapshot
    from cgi_pipeline.contracts import resolve_mode, validate_params
    specs, _ = snapshot()
    calls = []
    for step in steps:
        spec = specs[step['api_id']]
        params = validate_params(spec, step.get('parameters') or {})
        calls.append((spec, params, resolve_mode(spec, params)))
    return calls


def audit_only_operation(payload: dict, kind: str, *, _calls=None) -> bool:
    """Use the existing audit for session/service calls with no file workspace.

    Decide from catalog contracts on both submission and execution; never trust
    a caller-supplied skip flag. Explicit workspace/source requests retain the
    normal isolated-file path. Background Maya/Blender own isolated scenes.
    """
    if (payload.get('workspace_id') or payload.get('run_dir')
            or payload.get('source_path')
            or payload.get('scene_action') == 'open' or kind == 'workflow'):
        return False
    calls = _operation_contracts(payload, kind) if _calls is None else _calls
    return bool(calls) and all(spec['executor'] == 'ue' or
               (spec['executor'] == 'pipeline' and spec.get('access') == 'read')
               for spec, _, _ in calls)


def start_operation(payload: dict, kind: str, *, foreground: bool = False) -> dict:
    # Stable workspaces share a fence with relocation; no filesystem move can
    # race a new submission after its activity check. Anonymous allocation is
    # unchanged and is not accepted by the relocation API.
    identifier = payload.get('workspace_id') or payload.get('business_task_id')
    if identifier:
        from cgi_pipeline.core.workspace_relocation import workspace_fence, assert_submission_location
        from cgi_pipeline.core.workspace_relocation_evidence import needs_workspace_fence
        if not needs_workspace_fence(payload, kind, foreground):
            return _start_operation(payload, kind, foreground=foreground)
        project = workspace_project(payload)
        with workspace_fence(project, identifier):
            assert_submission_location(project, identifier)
            return _start_operation(payload, kind, foreground=foreground)
    return _start_operation(payload, kind, foreground=foreground)


def _start_operation(payload: dict, kind: str, *, foreground: bool = False) -> dict:
    """Bind one unique invocation to an existing or new business workspace.

    Ordinary foreground calls retain their existing no-sandbox behavior.
    The returned workspace_id, plus its project, is sufficient for later calls.
    """
    requested = str(payload.get('workspace_id') or '')
    business_task_id = str(payload.get('business_task_id') or '')
    if business_task_id:
        _validate_id(business_task_id)
    task_id = str(payload['task_id'])
    _validate_id(task_id)
    project = workspace_project(payload)
    if foreground and not requested:
        return workspace_result(payload)
    calls = _operation_contracts(payload, kind)
    if audit_only_operation(payload, kind, _calls=calls):
        return workspace_result(payload)

    business_run_dir = None
    if business_task_id and not requested:
        try:
            business_run_dir = _resolve(business_task_id, project, business_task_id)
            requested = business_task_id
        except WorkspaceError as exc:
            if exc.code != 'WORKSPACE_NOT_FOUND':
                raise
    if not requested and not payload.get('source_path') and payload.get('scene_action') != 'open':
        # Intent is a conservative initialization policy, never a permission or
        # proof of zero side effects. Keep the complete collection boundary.
        if calls and all(spec['executor'] in ('maya', 'blender') and mode in ('inspect', 'preview')
                         for spec, _, mode in calls):
            raise WorkspaceError('SCENE_TARGET_REQUIRED',
                                 'Select a foreground session, pass source_path or workspace_id, '
                                 'or explicitly use scene_action=open for a new isolated scene.')
    if requested:
        run_dir = business_run_dir or _resolve(requested, project, business_task_id)
    elif business_task_id:
        requested = business_task_id
        try:
            run_dir = _resolve(requested, project, business_task_id)
        except WorkspaceError as exc:
            if exc.code != 'WORKSPACE_NOT_FOUND':
                raise
            run_dir = run_archive.create_run_dir(
                requested, project, payload.get('asset_name', 'untitled'),
                payload.get('submitted_at'), directory_name=business_task_id,
                business_task_id=business_task_id,
            )
            run_dir = _resolve(requested, project, business_task_id)
    else:
        requested = task_id
        run_dir = run_archive.create_run_dir(task_id, project, payload.get('asset_name', 'untitled'),
                                             payload.get('submitted_at'))
        run_dir = _resolve(requested, project)
    operation_paths(run_dir, task_id)
    with _lock(run_dir):
        paths = operation_paths(run_dir, task_id)
        operation_dir = paths['operation_dir']
        targets = [run_dir / name for name in ('REPORT.md', 'REPORT.md.tmp', '.info/run_state.json.tmp')]
        targets += [path for key, path in paths.items() if key != 'operation_dir']
        targets += [path.with_name(path.name + '.tmp') for key, path in paths.items() if key != 'operation_dir']
        for target in targets:
            if target.is_symlink() or not _resolved_path(target).is_relative_to(_resolved_path(run_dir)):
                raise WorkspaceError('WORKSPACE_PATH_INVALID', f'Workspace output must be a local file: {target}')
        operation_dir.mkdir(parents=True, exist_ok=True)
        state_path = run_dir / '.info' / 'run_state.json'
        state = _read(state_path)
        state.update(workspace_mode=True, last_activity=time.time())
        if business_task_id:
            state['business_task_id'] = business_task_id
        state.setdefault('scene_hosts', {})
        _write(state_path, state)
        asset_name = payload.get('asset_name')
        if not asset_name or asset_name == 'untitled':
            asset_name = state.get('asset_name') or 'untitled'
        payload.update(workspace_id=requested, business_task_id=business_task_id,
                       workspace_project=project,
                       run_dir=str(run_dir),
                       asset_name=asset_name,
                       info_dir=str(run_dir / '.info'), operation_dir=str(operation_dir),
                       report_path=str(paths['report']),
                       workspace_report_path=str(run_dir / 'REPORT.md'))
        record_path = paths['state']
        if not record_path.exists():
            _write(record_path, {**workspace_result(payload), 'task_id': task_id,
                                'kind': kind, 'api_id': payload.get('api_id', ''),
                                'workflow_id': payload.get('workflow_id', ''), 'status': 'SUBMITTED',
                                'submitted_at': payload.get('submitted_at') or time.time()})
        _render_summary(run_dir, state)
    return workspace_result(payload)


def finish_operation(payload: dict, status: str) -> None:
    if not payload.get('workspace_report_path') or payload.get('_workspace_subchain'):
        return
    run_dir = Path(payload['run_dir'])
    with _lock(run_dir):
        record_path = operation_paths(run_dir, payload['task_id'])['state']
        record = _read(record_path)
        record.update(status=status, updated_at=time.time())
        _write(record_path, record)
        state_path = run_dir / '.info' / 'run_state.json'
        state = _read(state_path)
        state['last_activity'] = time.time()
        _write(state_path, state)
        _render_summary(run_dir, state)


def get_scene_binding(run_dir: str | Path, dcc: str) -> dict:
    from cgi_pipeline.core.workspace_relocation_view import scene_binding
    return scene_binding(run_dir, dcc)


def record_scene_binding(run_dir: str | Path, dcc: str, binding: dict) -> None:
    run_dir = Path(run_dir)
    with _lock(run_dir):
        path = run_dir / '.info' / 'run_state.json'
        state = _read(path)
        state.setdefault('scene_hosts', {})[dcc] = dict(binding)
        state['last_activity'] = time.time()
        _write(path, state)
