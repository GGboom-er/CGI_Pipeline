"""Initialize a workspace scene once and reuse only its live owning worker."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

from cgi_pipeline.core import dcc_factory, workspace
from cgi_pipeline.core.workspace import WorkspaceError, get_scene_binding, record_scene_binding


def invalidate_scene(worker) -> None:
    if worker is not None:
        getattr(worker, 'real_worker', worker)._scene_workspace_id = None


def _active_worker(dcc: str, workspace_id: str, binding: dict) -> bool:
    entry = dcc_factory._warm_pool.get(dcc)
    if not entry:
        return False
    worker = entry['worker']
    process = getattr(worker, 'process', None)
    return (getattr(worker, '_scene_workspace_id', None) == workspace_id
            and worker.worker_id == binding.get('worker_id')
            and (process is None or process.poll() is None))


def _read_staged_origins(run_dir: Path) -> dict:
    path = run_dir / '.info' / 'staged_origins.json'
    try:
        origins = workspace._read(path) if path.exists() else {}
        for filename, origin in origins.items():
            if (not isinstance(filename, str) or not filename or filename in ('.', '..')
                    or '/' in filename or '\\' in filename
                    or not isinstance(origin, str) or not origin or not Path(origin).is_absolute()):
                raise ValueError('Expected filename -> absolute origin directory mapping')
        return origins
    except (OSError, ValueError, WorkspaceError) as exc:
        raise WorkspaceError('WORKSPACE_STAGE_PROVENANCE_FAILED',
                             f'Cannot read valid staged-origin provenance: {path}: {exc}') from exc


def _record_staged_origin(source: Path, target: Path, run_dir: Path, origins: dict) -> None:
    origin = source.parent.as_posix()
    if (origins.get(target.name)
            and _path_identity(origins[target.name]) == _path_identity(source.parent)):
        return
    path = run_dir / '.info' / 'staged_origins.json'
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        workspace._write(path, {**origins, target.name: origin})
    except (OSError, ValueError, WorkspaceError) as exc:
        raise WorkspaceError('WORKSPACE_STAGE_PROVENANCE_FAILED',
                             f'Cannot record staged-origin provenance; source will not be opened: {path}: {exc}') from exc


def _path_identity(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _same_contents(source: Path, target: Path) -> bool:
    # filecmp caches by stat signature even with shallow=False. Publishing can
    # preserve file size/mtime, so snapshot reuse must compare current bytes.
    try:
        if source.stat().st_size != target.stat().st_size:
            return False
        with source.open('rb') as source_file, target.open('rb') as target_file:
            while chunk := source_file.read(1024 * 1024):
                if chunk != target_file.read(len(chunk)):
                    return False
            return not target_file.read(1)
    except OSError as exc:
        raise WorkspaceError('WORKSPACE_STAGE_FAILED',
                             f'Failed to compare staged source; original will not be opened: {exc}') from exc


def _stage(source_path: str, run_dir: Path) -> tuple[str, bool]:
    source = Path(source_path).resolve()
    if not source.is_file():
        raise WorkspaceError('WORKSPACE_SOURCE_MISSING', f'Source file not found: {source}')
    origins = _read_staged_origins(run_dir)
    if source.is_relative_to(run_dir.resolve()):
        # A staged/generated input is not evidence of a new external origin.
        return str(source), False
    # Directory provenance alone cannot identify a legacy collision alias's
    # original filename. New snapshots bind their name to the complete source
    # path, leaving the existing filename -> origin-directory registry intact.
    identity = _path_identity(source)
    source_key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
    snapshot_stem = f'{source.stem[:80]}_input_{source_key}'
    # Prefer the published spelling. A path fingerprint is only a collision
    # identity, never the normal user-facing filename or version.
    registered = next((name for name in origins
                       if os.path.normcase(name) == os.path.normcase(source.name)), source.name)
    target = run_dir / registered
    sequence = 1
    while target.exists() or target.name in origins:
        origin = origins.get(target.name)
        if (origin and _path_identity(origin) == _path_identity(source.parent)
                and target.is_file() and _same_contents(source, target)):
            break
        # An identical file with unknown/different provenance is not proof that
        # relative resources belong to this source; preserve it without reuse.
        suffix = '' if sequence == 1 else f'_{sequence - 1:03d}'
        target = run_dir / f'{snapshot_stem}{suffix}{source.suffix}'
        sequence += 1
    copied = not target.exists()
    if copied:
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            raise WorkspaceError('WORKSPACE_STAGE_FAILED', f'Failed to stage source; original will not be opened: {exc}') from exc
    _record_staged_origin(source, target, run_dir, origins)
    return str(target), copied


def source_display_name(source_path: str, run_dir: Path) -> str:
    """Use proven source identity for output naming, never strip arbitrary text."""
    source = Path(source_path)
    if source.parent.resolve() != run_dir.resolve():
        return source.name
    origins = _read_staged_origins(run_dir)
    origin = origins.get(source.name)
    if origin and re.search(r'_input_[0-9a-f]{64}(?:_\d{3,})?$', source.stem, re.IGNORECASE):
        for candidate in Path(origin).glob('*'):
            if not candidate.is_file():
                continue
            identity = _path_identity(candidate)
            digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()
            prefix = f'{Path(identity).stem[:80]}_input_{digest}'
            if re.fullmatch(re.escape(prefix) + r'(?:_\d{3,})?', source.stem, re.IGNORECASE):
                return candidate.name
        raise WorkspaceError('WORKSPACE_SOURCE_NAME_UNKNOWN',
                             'Cannot recover the published filename from staged provenance.')
    return source.name


def prepare_scene(payload: dict, dcc: str, create_worker, open_source_file, audit) -> tuple:
    """Continue never reloads. Explicit scene_action=open starts/reopens a scene.

    A persisted binding is evidence of prior initialization, not evidence that
    the corresponding in-memory scene survived a crash or another workspace.
    """
    workspace_id = payload['workspace_id']
    run_dir = Path(payload['run_dir'])
    source = str(payload.get('source_path') or '')
    if dcc not in ('maya', 'blender'):
        return create_worker(dcc, source), source
    binding = _scene_binding(payload, dcc)
    reopen = payload.get('scene_action', 'continue') == 'open'
    runtime = {key: payload['runtime'][key] for key in ('runtime_id', 'generation')} if payload.get('runtime') else {}
    if binding and not reopen:
        if runtime and any(binding.get(key) != value for key, value in runtime.items()):
            raise WorkspaceError('RUNTIME_GENERATION_MISMATCH',
                                 'Scene belongs to an older runtime. Explicitly open a saved source to rebuild.')
        if source and Path(source).resolve() not in {
            Path(value).resolve() for value in (binding.get('origin'), binding.get('source_path')) if value
        }:
            raise WorkspaceError('WORKSPACE_SOURCE_CHANGED', 'Use scene_action=open to explicitly replace the workspace scene.')
        if not _active_worker(dcc, workspace_id, binding):
            raise WorkspaceError('WORKSPACE_SCENE_UNAVAILABLE',
                                 'The workspace scene is no longer resident. Use scene_action=open with a saved source_path; prior APIs will not be replayed.')
        worker = create_worker(dcc, binding.get('source_path', ''), workspace_id=workspace_id)
        if (worker.worker_id != binding.get('worker_id')
                or getattr(getattr(worker, 'real_worker', worker), '_scene_workspace_id', None) != workspace_id
                or not _active_worker(dcc, workspace_id, binding)):
            raise WorkspaceError('WORKSPACE_SCENE_UNAVAILABLE',
                                 'The scene owner changed during acquisition. Explicitly open a saved source_path; no requested API was executed.')
        payload['scene_session'] = {'status': 'REUSED', 'staged': False, 'opened': False,
                                    'worker_id': worker.worker_id}
        audit('SCENE_REUSED', payload['scene_session'])
        return worker, binding.get('source_path', '')

    # Persist a pending initialization first: a failed attempt must not be
    # mistaken for a fresh workspace and implicitly retried on its next call.
    _scene_binding(payload, dcc, {'state': 'INITIALIZING', 'origin': source, **runtime})
    staged, copied = _stage(source, run_dir) if source else ('', False)
    worker = create_worker(dcc, staged, workspace_id=workspace_id)
    invalidate_scene(worker)
    audit('FILE_STAGED', {'origin': source, 'sandbox': staged, 'reused': not copied})
    ok, error = open_source_file(worker, payload['task_id'], staged, dcc)
    if not ok:
        raise WorkspaceError('WORKSPACE_SCENE_OPEN_FAILED', str(error))
    handle = getattr(worker, 'real_worker', worker)
    handle._scene_workspace_id = workspace_id
    _scene_binding(payload, dcc, {'state': 'READY', 'origin': source,
                                'source_path': staged, 'worker_id': worker.worker_id, **runtime})
    payload['scene_session'] = {'status': 'OPENED', 'staged': copied, 'opened': True,
                                'worker_id': worker.worker_id}
    audit('SCENE_OPENED', payload['scene_session'])
    return worker, staged


def _scene_binding(payload, dcc, binding=None):
    """Temporary hosts never replace the workspace's resident scene binding."""
    run_dir = Path(payload['run_dir'])
    lease = payload.get('runtime') or {}
    if lease.get('lifecycle') != 'workflow':
        if binding is not None:
            record_scene_binding(run_dir, dcc, binding)
            return binding
        return get_scene_binding(run_dir, dcc)
    operation = lease['operation_id']
    root_id = payload.get('workflow_id') if payload.get('_workspace_subchain') else payload['task_id']
    if root_id != operation:
        raise WorkspaceError('RUNTIME_BINDING_INVALID', 'Temporary scene belongs to another operation.')
    with workspace._lock(run_dir):
        path = workspace.operation_paths(run_dir, operation)['state']
        state = workspace._read(path)
        if state.get('task_id') != operation or state.get('workspace_id') != payload['workspace_id']:
            raise WorkspaceError('RUNTIME_BINDING_INVALID', 'Temporary scene state identity differs.')
        hosts = state.setdefault('scene_hosts', {})
        if binding is not None:
            hosts[dcc] = dict(binding)
            workspace._write(path, state)
        return dict(hosts.get(dcc) or {})
