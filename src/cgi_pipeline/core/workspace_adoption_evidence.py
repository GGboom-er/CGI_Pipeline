"""Read-only, uncached file evidence for explicit workspace adoption."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path, PurePosixPath

from cgi_pipeline.core import run_archive, workspace


def refuse(code, message):
    raise workspace.WorkspaceError('WORKSPACE_ADOPTION_' + code, message)


def relative_name(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/'))):
        refuse('PATH_INVALID', 'Evidence paths must be canonical relative POSIX paths.')
    return PurePosixPath(value)


def local_stat(path):
    info = Path(path).lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        refuse('PATH_INVALID', 'Reparse points and links cannot establish adoption ownership.')
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        refuse('PATH_INVALID', 'Only local regular files and directories are supported.')
    return info


def local_chain(path):
    path = Path(path)
    if not path.is_absolute():
        refuse('PATH_INVALID', 'An absolute managed path is required.')
    for item in reversed((path, *path.parents)):
        local_stat(item)
    return path


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, getattr(info, 'st_file_attributes', 0))


def file_bytes(path, *, limit=None):
    before = local_stat(path)
    if not stat.S_ISREG(before.st_mode) or limit is not None and before.st_size > limit:
        refuse('EVIDENCE_INVALID', 'Evidence file type or size is invalid.')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as handle:
        if _signature(os.fstat(handle.fileno())) != _signature(before):
            refuse('DRIFT', 'File identity changed before reading.')
        value = handle.read()
        if _signature(os.fstat(handle.fileno())) != _signature(before):
            refuse('DRIFT', 'File changed while reading.')
    if _signature(local_stat(path)) != _signature(before):
        refuse('DRIFT', 'File changed after reading.')
    return value


def digest_file(path):
    before = local_stat(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    digest = hashlib.sha256()
    with os.fdopen(descriptor, 'rb') as handle:
        if _signature(os.fstat(handle.fileno())) != _signature(before):
            refuse('DRIFT', 'Inventory file identity changed.')
        while block := handle.read(1024 * 1024):
            digest.update(block)
        if _signature(os.fstat(handle.fileno())) != _signature(before):
            refuse('DRIFT', 'Inventory file changed while hashing.')
    if _signature(local_stat(path)) != _signature(before):
        refuse('DRIFT', 'Inventory file changed after hashing.')
    return digest.hexdigest()


def read_sealed(root, relative, expected_digest):
    relative_name(relative)
    if not isinstance(expected_digest, str) or not re.fullmatch('[0-9a-f]{64}', expected_digest):
        refuse('EVIDENCE_INVALID', 'Evidence SHA256 is required.')
    path = Path(root) / relative
    local_chain(path)
    if not path.resolve().is_relative_to(Path(root).resolve()):
        refuse('PATH_INVALID', 'Evidence escaped the maintenance workspace.')
    raw = file_bytes(path, limit=16 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        refuse('DRIFT', 'Sealed evidence changed.')
    result = json.loads(raw)
    if not isinstance(result, dict):
        refuse('EVIDENCE_INVALID', 'Evidence must be an object.')
    return result


def target_path(project, business_task_id):
    workspace._validate_id(business_task_id)
    if (not isinstance(project, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', project)
            or project in ('.', '..') or run_archive._safe_segment(project) != project
            or run_archive._safe_segment(business_task_id) != business_task_id):
        refuse('IDENTITY_INVALID', 'Target identity cannot be truncated or normalized.')
    target = run_archive.PROJECT_ROOT / 'projects' / project / business_task_id
    local_chain(target)
    if not target.is_dir() or target.resolve() != target.absolute():
        refuse('PATH_INVALID', 'Target must already be a local managed directory.')
    return workspace._validate_workspace_location(target, project)


def inventory(root, *, ignored=(), deadline=None):
    root = Path(root)
    ignore, entries, observed = set(ignored), [], {}

    def visit(path):
        if deadline is not None and time.monotonic() > deadline:
            refuse('VALIDATION_TIMEOUT', 'Uncached evidence validation exceeded its budget.')
        before = local_stat(path)
        observed[path] = _signature(before)
        name = path.relative_to(root).as_posix()
        if stat.S_ISDIR(before.st_mode):
            if name != '.' and name not in ignore:
                entries.append({'path': name, 'kind': 'directory'})
            for child in sorted(path.iterdir()):
                visit(child)
            if _signature(local_stat(path)) != _signature(before):
                refuse('DRIFT', 'Directory changed during inventory.')
        elif name not in ignore:
            entries.append({'path': name, 'kind': 'file', 'size': before.st_size, 'sha256': digest_file(path)})
    visit(root)
    for path, signature in observed.items():
        if _signature(local_stat(path)) != signature:
            refuse('DRIFT', 'Previously inventoried content changed during validation.')
    if deadline is not None and time.monotonic() > deadline:
        refuse('VALIDATION_TIMEOUT', 'Uncached evidence validation exceeded its budget.')
    return sorted(entries, key=lambda row: row['path'])


def validate_inventory(manifest, target, project, business_task_id, *, ignored=(), deadline=None):
    if (type(manifest.get('schema_version')) is not int or manifest.get('schema_version') != 1
            or manifest.get('kind') != 'cgi_workspace_adoption_manifest'
            or manifest.get('target_project') != project or manifest.get('business_task_id') != business_task_id
            or Path(str(manifest.get('workspace_root', ''))).absolute() != target):
        refuse('IDENTITY_INVALID', 'Manifest target identity does not match.')
    rows = manifest.get('entries')
    if not isinstance(rows, list):
        refuse('EVIDENCE_INVALID', 'Manifest entries are required.')
    names, aliases, count, total = [], set(), 0, 0
    for row in rows:
        if not isinstance(row, dict):
            refuse('EVIDENCE_INVALID', 'Invalid manifest row.')
        name = str(relative_name(row.get('path')))
        alias = os.path.normcase(name)
        if alias in aliases:
            refuse('EVIDENCE_INVALID', 'Duplicate or aliased manifest paths.')
        aliases.add(alias)
        names.append(name)
        if row.get('kind') == 'file':
            if (set(row) != {'path', 'kind', 'size', 'sha256'} or type(row['size']) is not int
                    or row['size'] < 0 or not isinstance(row['sha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
                refuse('EVIDENCE_INVALID', 'Invalid file inventory row.')
            count += 1
            total += row['size']
        elif row.get('kind') != 'directory' or set(row) != {'path', 'kind'}:
            refuse('EVIDENCE_INVALID', 'Invalid directory inventory row.')
    if (names != sorted(names) or type(manifest.get('file_count')) is not int
            or type(manifest.get('total_bytes')) is not int
            or manifest.get('file_count') != count or manifest.get('total_bytes') != total
            or inventory(target, ignored=ignored, deadline=deadline) != rows):
        refuse('DRIFT', 'Target inventory or its summary changed.')
