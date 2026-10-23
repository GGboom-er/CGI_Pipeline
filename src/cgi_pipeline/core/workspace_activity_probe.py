"""Read existing process, Celery and Redis ownership; never start or consume work."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import psutil

from cgi_pipeline.core import run_archive, service_manager as services, workspace
from cgi_pipeline.core.runtime_pool import RuntimePool
from cgi_pipeline.core.runtime_services import RuntimeService
from cgi_pipeline.core.runtime_state import process_state, owned_inventory, publication_complete
from cgi_pipeline.core.task_audit import find_audit_file, read_entries, task_state
from cgi_pipeline.core.workspace_activity import REPLIES, MAX_ACTIVITY_LEASE_SEC, ownership_projection, validate_activity
from cgi_pipeline.core.workspace_adoption_evidence import file_bytes, local_chain, refuse


def _source(identifier, path, fingerprint='file_sha256'):
    path = local_chain(Path(path).absolute())
    raw = file_bytes(path, limit=16 * 1024 * 1024)
    value = ownership_projection(json.loads(raw)) if fingerprint == 'runtime_ownership_v1' else raw
    return {'id': identifier, 'path': str(path), 'size': len(value),
            'sha256': hashlib.sha256(value).hexdigest(), 'fingerprint': fingerprint}


def reconcile_processes(workers, leases, records):
    """Reconcile the current process universe; ordinary unmarked processes are irrelevant."""
    for process in psutil.process_iter(['pid', 'name', 'cmdline']):
        info = process.info
        command, name = info.get('cmdline'), str(info.get('name') or '').lower()
        if command is None:
            if name in ('maya.exe', 'blender.exe', 'python.exe', 'pythonw.exe', 'celery.exe'):
                refuse('ACTIVITY_UNKNOWN', 'Potential CGI process identity is unreadable.')
            continue
        cgi_worker = 'worker' in command and any('cgi_pipeline.core.tasks' in part for part in command)
        cgi_host = any('cgi_pipeline.hosts.' in part for part in command)
        if cgi_worker and info['pid'] not in workers:
            refuse('ACTIVITY_UNKNOWN', 'A live CGI worker is absent from the ownership ledger.')
        if not (cgi_worker or cgi_host or name in
                ('maya.exe', 'blender.exe', 'python.exe', 'pythonw.exe', 'mayapy.exe', 'celery.exe')):
            continue
        try:
            env = process.environ()
            marked = any(key in env for key in ('CGI_RUNTIME_ID', 'CGI_RUNTIME_ROOT', 'CGI_RUNTIME_GENERATION'))
            if marked:
                identity = {field: env.get(key) for key, field in
                    (('CGI_RUNTIME_ID', 'runtime_id'), ('CGI_RUNTIME_ROOT', 'root'),
                     ('CGI_RUNTIME_GENERATION', 'generation'))}
                lease = leases.get(identity['runtime_id'])
                record = records.get(info['pid'])
                if (lease is None or any(identity[key] != lease[key] for key in identity)
                        or record is None or process_state(record, lease) != 'LIVE'):
                    refuse('ACTIVITY_UNKNOWN', 'A marked CGI process has incomplete or orphaned ownership.')
            elif cgi_host and info['pid'] not in records:
                refuse('ACTIVITY_UNKNOWN', 'A CGI host has no verifiable process owner.')
        except (psutil.NoSuchProcess, ProcessLookupError):
            continue
        except (psutil.AccessDenied, PermissionError):
            refuse('ACTIVITY_UNKNOWN', 'CGI process environment is unreadable.')


def read_runtime_ownership(known_task_ids=(), *, pool=None):
    """File/process reads only; also used by the Hook's uncached validator."""
    from celery.utils.nodenames import host_format
    pool = pool or RuntimePool()
    result = {'sources': [], 'expected_nodes': [], 'known_task_ids': sorted(set(known_task_ids)),
              'known_tasks': {}, 'ledger_activity': [], 'process_inventory': [], 'unknown': []}
    try:
        result['sources'].append(_source('runtime_pool', pool.path, 'runtime_ownership_v1'))
        ledger = json.loads(file_bytes(pool.path))
        ownership_projection(ledger)
        if ledger['lost']:
            result['unknown'].append('lost_runtime_records')
        pidfile = services.RUNTIME_DIR / 'worker_cgi.pid'
        result['sources'].append(_source('legacy_pid', pidfile))
        process = psutil.Process(int(file_bytes(pidfile).decode().strip()))
        command = process.cmdline()
        if (not process.is_running() or os.path.normcase(process.exe()) != os.path.normcase(sys.executable)
                or 'worker' not in command or 'cgi_pipeline.core.tasks' not in command):
            refuse('ACTIVITY_UNKNOWN', 'Legacy process identity does not match its registered owner.')
        result['expected_nodes'].append(dict(node=host_format(services._worker_hostname('cgi')), role='legacy',
            queues=[services._queue_for_dcc('cgi')], source_ids=['legacy_pid'], runtime_id=None, generation=None,
            pid=process.pid, create_time=process.create_time(), cmdline=command))
        result['process_inventory'].append(dict(role='legacy', pid=process.pid,
            create_time=process.create_time(), cmdline=command))
        workers, accounted = {process.pid: True}, {}
        for key, row in sorted(ledger['slots'].items()):
            worker = RuntimeService._worker(row)
            if not worker or process_state(worker, row) != 'LIVE':
                refuse('ACTIVITY_UNKNOWN', 'A recorded slot has no confirmed live worker.')
            records = owned_inventory(row, worker)
            if (any(process_state(item, row) not in ('LIVE', 'DEAD') for item in records)
                    or not publication_complete(row)[0]):
                refuse('ACTIVITY_UNKNOWN', 'Slot process/publication accounting is incomplete.')
            workers[worker['pid']] = True
            accounted.update({record['pid']: record for record in records})
            result['process_inventory'].extend(dict(record, role='slot_process') for record in records)
            result['expected_nodes'].append(dict(node=RuntimeService._node(row), role='slot', queues=[row['queue']],
                source_ids=['runtime_pool'], runtime_id=key, generation=row['generation'],
                pid=worker['pid'], create_time=worker['create_time'], cmdline=worker['cmdline'],
                project=row['project'], workspace_id=row['workspace_id']))
            result['ledger_activity'].append({key: row[key] for key in
                ('runtime_id', 'project', 'workspace_id', 'state', 'active_task', 'pending')})
        reconcile_processes(workers, ledger['slots'], accounted)
        for task_id in result['known_task_ids']:
            workspace._validate_id(task_id)
            path = find_audit_file(task_id, run_archive.PROJECT_ROOT / 'audit')
            if path is None:
                refuse('ACTIVITY_UNKNOWN', 'A declared historical task has no original audit.')
            result['sources'].append(_source('audit:' + task_id, path))
            entries = read_entries(path)
            latest = task_state(entries)
            if latest.get('terminal') is not True or latest.get('task_id') != task_id:
                refuse('ACTIVITY_UNKNOWN', 'A declared historical task has no task-level terminal result.')
            entry = {k: v for k, v in latest.items() if k != 'terminal'}
            index = next(i for i in range(len(entries) - 1, -1, -1) if entries[i] == entry)
            result['known_tasks'][task_id] = dict(audit_source_id='audit:' + task_id, entry_index=index, entry=entry)
    except Exception as exc:
        result['unknown'].append('ownership:' + getattr(exc, 'code', type(exc).__name__))
    result['sources'].sort(key=lambda row: row['id'])
    result['expected_nodes'].sort(key=lambda row: row['node'])
    result['process_inventory'].sort(key=lambda row: row['pid'])
    return result


def broker_configuration(nodes, app=None):
    """Derive identity/priority coverage without opening a broker connection."""
    from kombu.transport.redis import Channel
    app = app or services.get_celery_app()
    connection = app.connection_for_read()
    if connection.transport.driver_type != 'redis' or set(app.conf.accept_content or []) != {'json'}:
        refuse('ACTIVITY_UNKNOWN', 'Only the registered JSON Redis transport is supported.')
    options = app.conf.broker_transport_options or {}
    prefix, steps = options.get('global_keyprefix', ''), options.get('priority_steps', Channel.priority_steps)
    if (not isinstance(prefix, str) or not isinstance(steps, (list, tuple)) or not steps
            or 0 not in steps or any(type(n) is not int or n < 0 for n in steps)
            or list(steps) != sorted(set(steps))):
        refuse('ACTIVITY_UNKNOWN', 'Unknown Redis priority configuration.')
    queues = {services._queue_for_dcc('cgi'), app.conf.task_default_queue}
    queues.update(queue for node in nodes for queue in node['queues'])
    if any(not isinstance(queue, str) or not queue for queue in queues):
        refuse('ACTIVITY_UNKNOWN', 'A configured queue identity is missing.')
    expected = sorted(prefix + queue + (Channel.sep + str(priority) if priority else '')
                      for queue in queues for priority in steps)
    return {'identity': {'host': connection.hostname, 'port': connection.port or 6379,
                         'db': int(connection.virtual_host or 0)}, 'expected_queue_keys': expected}


def _broker_read(channel, expected):
    import redis
    # Reuse Kombu's exact connection pool without its key-prefix wrapper.
    client = redis.Redis(connection_pool=channel.client.connection_pool)
    prefix = channel.global_keyprefix or ''
    if isinstance(prefix, bytes):
        prefix = prefix.decode('utf-8')
    keys, cursor, turns = set(), 0, 0
    while True:
        cursor, chunk = client.scan(cursor=cursor, count=500)
        keys.update(chunk)
        turns += 1
        if len(keys) > 50000 or turns > 1000:
            refuse('ACTIVITY_UNKNOWN', 'Broker key coverage exceeds the bounded probe.')
        if cursor == 0:
            break
    lists = {key.decode('utf-8') for key in keys if client.type(key) == b'list'}
    queue_keys = sorted(set(expected) | lists)
    ready, total = {}, 0
    for key in queue_keys:
        count = client.llen(key)
        if count > 10000:
            refuse('ACTIVITY_UNKNOWN', 'Broker queue exceeds the bounded probe.')
        raw = client.lrange(key, 0, count - 1) if count else []
        total += sum(len(value) for value in raw)
        if len(raw) != count or client.llen(key) != count or total > 16 * 1024 * 1024:
            refuse('ACTIVITY_UNKNOWN', 'Broker queue changed or exceeds the bounded probe.')
        ready[key] = [value.decode('utf-8') for value in raw]
    unacked, cursor, turns = {}, 0, 0
    unacked_count = client.hlen(prefix + channel.unacked_key)
    while True:
        cursor, chunk = client.hscan(prefix + channel.unacked_key, cursor=cursor, count=128)
        total += sum(len(value) for value in chunk.values())
        unacked.update({key.decode('utf-8'): value.decode('utf-8') for key, value in chunk.items()})
        turns += 1
        if len(unacked) > 10000 or turns > 1000 or total > 16 * 1024 * 1024:
            refuse('ACTIVITY_UNKNOWN', 'Broker unacked coverage exceeds the bounded probe.')
        if cursor == 0:
            break
    index = {key.decode('utf-8'): score for key, score in
             client.zrange(prefix + channel.unacked_index_key, 0, -1, withscores=True)}
    if (len(unacked) != unacked_count or client.hlen(prefix + channel.unacked_key) != unacked_count
            or len(index) > 10000 or client.zcard(prefix + channel.unacked_index_key) != len(index)):
        refuse('ACTIVITY_UNKNOWN', 'Broker unacked coverage changed or exceeds its bound.')
    return dict(ready=ready, unacked=unacked, unacked_index=index)


def collect_activity_snapshot(target_project, business_task_id, workspace_root, *, known_task_ids=(), now=None):
    started = time.time() if now is None else now
    ownership = read_runtime_ownership(known_task_ids)
    result = dict(schema_version=1, kind='cgi_workspace_adoption_activity', target_project=target_project,
                  business_task_id=business_task_id, workspace_root=str(workspace_root),
                  captured_at_epoch=started, expires_at_epoch=started + MAX_ACTIVITY_LEASE_SEC, **ownership)
    result['replies'], result['broker'] = {}, {}
    connection = channel = None
    try:
        if ownership['unknown']:
            refuse('ACTIVITY_UNKNOWN', 'Process/source ownership is incomplete.')
        app = services.get_celery_app()
        configuration = broker_configuration(ownership['expected_nodes'], app)
        nodes = [row['node'] for row in ownership['expected_nodes']]
        for kind in REPLIES:
            inspector = app.control.inspect(destination=nodes, timeout=2)
            result['replies'][kind] = (getattr(inspector, kind)(safe=True) if kind == 'active'
                                       else getattr(inspector, kind)()) or {}
        connection = app.connection_for_read()
        connection.ensure_connection(max_retries=0)
        channel = connection.channel()
        first = _broker_read(channel, configuration['expected_queue_keys'])
        second = _broker_read(channel, configuration['expected_queue_keys'])
        if first != second:
            refuse('ACTIVITY_UNKNOWN', 'Broker records changed during the snapshot.')
        result['broker'] = dict(transport='redis', **configuration, **second)
        after = read_runtime_ownership(known_task_ids)
        if any(ownership[key] != after[key] for key in ('sources', 'expected_nodes', 'known_tasks', 'process_inventory')):
            refuse('ACTIVITY_UNKNOWN', 'Ownership changed during the snapshot.')
        result['ledger_activity'] = after['ledger_activity']
        result['unknown'].extend(after['unknown'])
        validate_activity(result, target_project, business_task_id, Path(workspace_root), after, configuration, now=now)
    except Exception as exc:
        result['unknown'].append('probe:' + getattr(exc, 'code', type(exc).__name__))
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception as exc:
                result['unknown'].append('channel_close:' + type(exc).__name__)
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                result['unknown'].append('connection_close:' + type(exc).__name__)
    return result
