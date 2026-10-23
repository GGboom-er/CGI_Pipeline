"""Validate a sealed human scope statement; never present it as a scene probe."""
from __future__ import annotations

import math
import os
import time
from pathlib import Path

from cgi_pipeline.core import run_archive, workspace

MAX_CONFIRMATION_WINDOW_SEC = 4 * 60 * 60
KIND = 'cgi_workspace_foreground_scope_confirmation'


def _refuse(message):
    raise workspace.WorkspaceError('WORKSPACE_RELOCATION_FOREGROUND_UNKNOWN', message)


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _absolute(value):
    if (not isinstance(value, str) or not value or not Path(value).is_absolute()
            or any(part in ('.', '..') for part in value.replace('\\', '/').split('/'))):
        _refuse('Foreground confirmation requires normalized absolute paths.')
    return os.path.normcase(os.path.normpath(value))


def validate_confirmation(value, roots, maintenance, *, allow_expired=False):
    """The approval window is fixed by the sealed manifest, never by activity TTL."""
    if value is None:
        return None
    required = {'schema_version', 'kind', 'maintenance_root', 'recorded_at', 'expires_at',
                'statement', 'evidence', 'processes', 'scopes'}
    if (not isinstance(value, dict) or set(value) != required
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('kind') != KIND):
        _refuse('Foreground confirmation has an invalid schema.')
    if maintenance is None or value['maintenance_root'] != str(maintenance):
        _refuse('Foreground confirmation belongs to another maintenance workspace.')
    now = time.time()
    recorded, expires = value['recorded_at'], value['expires_at']
    if (not _number(recorded) or not _number(expires) or not math.isfinite(now)
            or recorded > now or (expires <= now and not allow_expired) or expires <= recorded
            or expires - recorded > MAX_CONFIRMATION_WINDOW_SEC):
        _refuse('Foreground confirmation is future-dated, expired or outside its fixed window.')
    if any(not isinstance(value[key], str) or not value[key].strip() for key in ('statement', 'evidence')):
        _refuse('Foreground confirmation needs the human statement and its source.')
    processes = value['processes']
    if not isinstance(processes, list) or not processes:
        _refuse('Foreground confirmation must identify exact processes.')
    pids = set()
    for row in processes:
        if (not isinstance(row, dict) or set(row) != {'pid', 'create_time', 'exe'}
                or type(row.get('pid')) is not int or row['pid'] <= 0 or row['pid'] in pids
                or not _number(row.get('create_time')) or row['create_time'] > recorded):
            _refuse('Foreground confirmation process identities are invalid or repeated.')
        _absolute(row['exe'])
        pids.add(row['pid'])
    scopes = value['scopes']
    if not isinstance(scopes, list) or not scopes:
        _refuse('Foreground confirmation must name exact relocation root pairs.')
    seen = set()
    base = run_archive.PROJECT_ROOT / 'projects'
    for row in scopes:
        if not isinstance(row, dict) or set(row) != {'source_root', 'target_root'}:
            _refuse('Foreground confirmation scope has an invalid schema.')
        source, target = (Path(row[key]) if isinstance(row[key], str) else None
                          for key in ('source_root', 'target_root'))
        for key, path in (('source_root', source), ('target_root', target)):
            _absolute(row[key])
            if path.parent.parent != base or str(path) != row[key]:
                _refuse('Foreground confirmation scopes must be canonical managed roots.')
        pair = (row['source_root'], row['target_root'])
        if (source.name != target.name or source == target or maintenance in (source, target)
                or pair in seen):
            _refuse('Foreground confirmation scope is ambiguous or repeated.')
        seen.add(pair)
    if tuple(str(root) for root in roots) not in seen:
        _refuse('Foreground confirmation does not cover this exact source and target pair.')
    # Recovery may discard only an otherwise valid expired human statement.
    # The caller must still inspect every current foreground process/session.
    return None if expires <= now else value


def process_confirmation(process, confirmation):
    """Only ordinary unregistered Maya/Blender may use human scope evidence."""
    import psutil
    if confirmation is None:
        _refuse('A foreground process has no inspectable registered endpoint or human scope confirmation.')
    row = next((row for row in confirmation['processes'] if row['pid'] == process.info['pid']), None)
    if row is None:
        _refuse('An unknown or newly started foreground process is outside the confirmation.')
    try:
        created, executable = process.create_time(), process.exe()
        command, environment = process.cmdline(), process.environ()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError, PermissionError) as exc:
        _refuse('Foreground process identity could not be verified: ' + type(exc).__name__)
    if (not _number(created) or created != row['create_time']
            or _absolute(executable) != _absolute(row['exe'])):
        _refuse('Foreground process identity changed after human confirmation.')
    if (not isinstance(command, list) or not command or not isinstance(environment, dict)
            or any(not isinstance(part, str) for part in command)
            or any(key in environment for key in
                   ('CGI_RUNTIME_ID', 'CGI_RUNTIME_GENERATION', 'CGI_RUNTIME_ROOT', 'CGI_WORKER_ID'))
            or any('cgi_pipeline.hosts.' in part.lower() for part in command)
            or ('worker' in command and any('cgi_pipeline.core.tasks' in part for part in command))):
        _refuse('CGI-owned or unreadable process identity cannot use human scope confirmation.')
    return dict(evidence_kind='operator_recorded_confirmation', **row,
        maintenance_root=confirmation['maintenance_root'], statement=confirmation['statement'],
        evidence=confirmation['evidence'], recorded_at=confirmation['recorded_at'],
        expires_at=confirmation['expires_at'], scopes=confirmation['scopes'])
