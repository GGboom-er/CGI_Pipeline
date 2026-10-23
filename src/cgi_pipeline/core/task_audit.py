"""Shared task audit lookup and task-level state projection."""
import json
import os
from pathlib import Path

from cgi_pipeline.core.task_status import TERMINAL_STATUSES


def _valid_task_id(task_id):
    return bool(task_id) and all(
        c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in task_id)


def _audit_candidates(root):
    """Enumerate the root once, sharing dated-first precedence with single lookup."""
    try:
        entries = list(root.iterdir())
    except FileNotFoundError:
        return [], []
    # Most entries are flat audit files. Do not stat each as a possible directory.
    dated = sorted((path for path in entries if len(path.name) == 10 and path.is_dir()), reverse=True)
    return dated, entries


def find_audit_file(task_id, root):
    root = Path(root)
    if not _valid_task_id(task_id):
        return None
    dated, _ = _audit_candidates(root)
    for directory in dated:
        candidate = directory / f'{task_id}.json'
        if candidate.exists():
            return candidate
    flat = root / f'{task_id}.json'
    return flat if flat.exists() else None


def find_audit_files(root):
    """Return each known task path once: newest dated copy, then legacy flat copy."""
    dated, flat_entries = _audit_candidates(Path(root))
    files = {}

    def collect(entries):
        for path in entries:
            if path.suffix == '.json' and _valid_task_id(path.stem):
                files.setdefault(path.stem, path)

    for directory in dated:
        try:
            collect(directory.iterdir())
        except FileNotFoundError:
            continue  # Another process may have moved a dated directory.
    collect(flat_entries)
    return files


def read_entries(path):
    if not path or not Path(path).exists():
        return []
    entries = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # A concurrent append may not have finished its line yet.
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def prepare_adoption_route(task_id, root, route):
    """Durably route the original control ID before its workspace commit."""
    if not _valid_task_id(task_id) or find_audit_file(task_id, root) is not None:
        raise ValueError('Adoption requires a fresh server-generated operation ID.')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    from cgi_pipeline.core.workspace_adoption_evidence import local_chain
    local_chain(root.absolute())
    path = root / (task_id + '.json')
    row = dict(route, task_id=task_id, kind='workspace_adoption_route', status='ADOPTION_PREPARED')
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return row


def read_adoption_route(task_id, root):
    """Only the exact durable first line is a route; later ERROR/partial appends cannot replace it."""
    if not _valid_task_id(task_id):
        return None
    path = Path(root) / (task_id + '.json')
    try:
        from cgi_pipeline.core.workspace_adoption_evidence import local_chain
        local_chain(path.absolute())
        with path.open('rb') as stream:
            raw = stream.readline(65537)
    except FileNotFoundError:
        return None
    if len(raw) > 65536 or not raw.endswith(b'\n'):
        return None
    try:
        row = json.loads(raw)
    except ValueError:
        return None
    return row if isinstance(row, dict) and row.get('kind') == 'workspace_adoption_route' else None


def prepare_relocation_route(task_id, root, route):
    """Publish a separate control operation route, never append to old tasks."""
    if not _valid_task_id(task_id) or find_audit_file(task_id, root) is not None:
        raise ValueError('Relocation requires a fresh server-generated operation ID.')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    from cgi_pipeline.core.workspace_adoption_evidence import local_chain
    local_chain(root.absolute())
    row = {**route, 'task_id': task_id, 'kind': 'workspace_relocation_route', 'status': 'RELOCATION_PREPARED'}
    with (root / (task_id + '.json')).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return row


def read_relocation_route(task_id, root):
    if not _valid_task_id(task_id):
        return None
    path = Path(root) / (task_id + '.json')
    try:
        from cgi_pipeline.core.workspace_adoption_evidence import local_chain
        local_chain(path.absolute())
        with path.open('rb') as stream:
            raw = stream.readline(65537)
    except FileNotFoundError:
        return None
    if len(raw) > 65536 or not raw.endswith(b'\n'):
        return None
    try:
        row = json.loads(raw)
    except ValueError:
        return None
    return row if isinstance(row, dict) and row.get('kind') == 'workspace_relocation_route' else None


def task_state(entries):
    """Completion beats a racing request; progress cannot erase a request.

    New executors delimit an attempt with TASK_RUNNING and a scope=task result.
    Legacy records retain their existing terminal interpretation.
    """
    start = next((i for i in range(len(entries) - 1, -1, -1)
                  if entries[i].get('status') == 'TASK_RUNNING'), 0)
    attempt = entries[start:]
    modern = bool(attempt and attempt[0].get('status') == 'TASK_RUNNING')
    terminal_candidates = reversed(attempt) if modern else [next(
        (entry for entry in reversed(attempt) if entry.get('status') != 'CANCEL_REQUESTED'), {})]
    for entry in terminal_candidates:
        if entry.get('status') in TERMINAL_STATUSES and (
            entry.get('scope') == 'task' or (not modern and entry.get('scope') != 'segment')
        ):
            return {**entry, 'terminal': True}
    pending_start = next((i + 1 for i in range(start - 1, -1, -1)
                          if entries[i].get('scope') == 'task'
                          and entries[i].get('status') in TERMINAL_STATUSES), 0)
    for entry in reversed(entries[pending_start:]):
        if entry.get('status') == 'CANCEL_REQUESTED':
            return {**entry, 'terminal': False}
    latest = attempt[-1] if attempt else {'status': 'UNKNOWN'}
    if modern and latest.get('status') in TERMINAL_STATUSES:
        latest = {**latest, 'status': 'RUNNING'}
    return {**latest, 'terminal': False}
