"""Read and verify process ownership without a global process/orphan scan."""
from __future__ import annotations

import json
import math
from pathlib import Path

import psutil

from cgi_pipeline.core.runtime_pool import RuntimePoolError


RUNTIME_ENV = {'CGI_RUNTIME_ID': 'runtime_id', 'CGI_RUNTIME_GENERATION': 'generation',
               'CGI_RUNTIME_ROOT': 'root'}


def snapshot(pid, process_factory=psutil.Process):
    try:
        process = process_factory(int(pid))
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        env = process.environ()
        return {'pid': int(pid), 'create_time': process.create_time(),
                'cmdline': process.cmdline(),
                **{field: env.get(key) for key, field in RUNTIME_ENV.items()}}
    except (psutil.NoSuchProcess, ProcessLookupError):
        return None
    except (psutil.AccessDenied, PermissionError) as exc:
        raise RuntimePoolError('RUNTIME_IDENTITY_UNKNOWN', f'Cannot inspect process {pid}.') from exc


def matches_lease(identity, lease):
    return all(identity.get(field) == lease[field] for field in RUNTIME_ENV.values())


def process_state(identity, lease, process_factory=psutil.Process):
    if not matches_lease(identity, lease):
        return 'FOREIGN'
    current = snapshot(identity['pid'], process_factory)
    if current is None:
        return 'DEAD'
    if (not matches_lease(current, lease)
            or abs(current['create_time'] - identity['create_time']) > 0.001
            or current['cmdline'] != identity['cmdline']):
        return 'FOREIGN'
    return 'LIVE'


def read_identity(path, lease):
    if path.is_symlink():
        raise RuntimePoolError('RUNTIME_IDENTITY_UNKNOWN', 'Process metadata cannot be a link.')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Process metadata must be an object.')
        if not isinstance(value.get('generation'), str) or not value['generation']:
            raise ValueError('Process generation is missing.')
        if value['generation'] != lease['generation']:
            return None  # Retained evidence from a previously released generation.
        if (not matches_lease(value, lease) or type(value.get('pid')) is not int
                or not isinstance(value.get('create_time'), (int, float))
                or not math.isfinite(value['create_time'])
                or not isinstance(value.get('cmdline'), list) or not value['cmdline']
                or any(not isinstance(item, str) for item in value['cmdline'])):
            raise ValueError('Incomplete or foreign process identity.')
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimePoolError('RUNTIME_IDENTITY_UNKNOWN', str(exc)) from exc


def owned_inventory(lease, worker, process_factory=psutil.Process):
    """Combine published hosts with live descendants of the exact worker."""
    records = {worker['pid']: worker} if worker else {}
    for path in (Path(lease['root']) / 'hosts').glob('*.json'):
        value = read_identity(path, lease)
        if value:
            records[value['pid']] = value
    if worker and process_state(worker, lease, process_factory) == 'LIVE':
        try:
            children = process_factory(worker['pid']).children(recursive=True)
        except (psutil.AccessDenied, PermissionError) as exc:
            raise RuntimePoolError('RUNTIME_IDENTITY_UNKNOWN', 'Cannot inspect owned descendants.') from exc
        except (psutil.NoSuchProcess, ProcessLookupError):
            children = []
        for child in children:
            value = snapshot(child.pid, process_factory)
            if value and not matches_lease(value, lease):
                raise RuntimePoolError('RUNTIME_IDENTITY_UNKNOWN', 'A descendant lacks exact runtime ownership.')
            if value:
                records.setdefault(child.pid, value)
    return list(records.values())


def publication_complete(lease):
    """Prove managed-host launch records complete; no global process scan."""
    if lease.get('host_publication_version') != 1:
        return False, 'Runtime predates durable host launch publication.'
    try:
        published = set()
        for path in (Path(lease['root']) / 'host_launches').glob('*.json'):
            if path.is_symlink():
                return False, 'Host launch metadata is a link.'
            intent = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(intent.get('generation'), str) or not intent['generation']:
                return False, 'Host launch generation is missing or invalid.'
            if intent.get('generation') != lease['generation']:
                continue
            if (not matches_lease(intent, lease) or intent.get('state') != 'COMMITTED'
                    or intent.get('worker_id') != path.stem or intent.get('host') not in ('maya', 'blender')):
                return False, 'Host launch outcome is uncommitted.'
            host = read_identity(Path(lease['root']) / 'hosts' / path.name, lease)
            if not host or any(host.get(key) != intent.get(key) for key in ('pid', 'create_time', 'cmdline', 'worker_id')):
                return False, 'Host launch has no matching ownership record.'
            published.add(path.name)
        for path in (Path(lease['root']) / 'hosts').glob('*.json'):
            if read_identity(path, lease) and path.name not in published:
                return False, 'Host ownership has no committed launch intent.'
        return True, ''
    except (OSError, ValueError, AttributeError, RuntimePoolError) as exc:
        return False, str(exc)
