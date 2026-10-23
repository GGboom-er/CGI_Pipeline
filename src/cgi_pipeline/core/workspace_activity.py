"""Bounded JSON activity interpretation; no broker connection or mutation."""
from __future__ import annotations

import ast
import base64
import json
import math
import os
import zlib
from pathlib import Path

from cgi_pipeline.core import workspace, run_archive
from cgi_pipeline.core.workspace_adoption_evidence import refuse

LIMIT = 1024 * 1024
MAX_ACTIVITY_LEASE_SEC = 300
REPLIES = ('active', 'reserved', 'scheduled', 'active_queues')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def ownership_projection(ledger):
    if (not isinstance(ledger, dict) or set(ledger) != {'version', 'slots', 'lost'}
            or type(ledger['version']) is not int or ledger['version'] != 1
            or not all(isinstance(ledger[k], dict) for k in ('slots', 'lost'))):
        refuse('ACTIVITY_UNKNOWN', 'Unsupported runtime ownership schema.')
    result = {'version': 1, 'slots': {}, 'lost': {}}
    for section in ('slots', 'lost'):
        for key, row in sorted(ledger[section].items()):
            if not isinstance(row, dict) or not all(k in row for k in (
                    'runtime_id', 'generation', 'root', 'queue', 'project', 'workspace_id', 'worker', 'state')):
                refuse('ACTIVITY_UNKNOWN', 'Incomplete runtime ownership identity.')
            allowed = {'runtime_id', 'generation', 'root', 'queue', 'project', 'workspace_id', 'worker',
                       'state', 'pending', 'active_task', 'created_at', 'updated_at', 'lost_at', 'threads',
                       'host_publication_version', 'release_state', 'abandoned_tasks', 'publication_unknown'}
            if (set(row) - allowed or (section == 'slots' and key != row['runtime_id'])
                    or row['state'] not in ('STARTING', 'IDLE', 'BUSY', 'DRAINING', 'LOST')
                    or any(not isinstance(row[k], str) or not row[k] for k in
                           ('runtime_id', 'generation', 'root', 'queue', 'project', 'workspace_id'))
                    or not isinstance(row.get('pending'), dict) or not isinstance(row.get('active_task'), str)):
                refuse('ACTIVITY_UNKNOWN', 'Unknown or incomplete runtime row schema.')
            projected = {k: v for k, v in row.items() if k not in (
                'pending', 'active_task', 'created_at', 'updated_at', 'lost_at')}
            # BUSY and IDLE have the same resource owner; lifecycle transitions do not.
            if row['state'] in ('BUSY', 'IDLE'):
                projected['state'] = 'OWNED'
            result[section][key] = projected
    return canonical(result)


def literal(value):
    if not isinstance(value, str):
        return value
    if len(value) > LIMIT:
        refuse('ACTIVITY_UNKNOWN', 'Task argument text exceeds the inspection bound.')
    try:
        return json.loads(value)
    except ValueError:
        tree = ast.parse(value, mode='eval')
        if sum(1 for _ in ast.walk(tree)) > 30000:
            refuse('ACTIVITY_UNKNOWN', 'Task argument syntax exceeds the inspection bound.')
        return ast.literal_eval(tree)


def classify(value, project, business_task_id, target):
    target_text = str(target).replace('\\', '/').rstrip('/').casefold()
    owners, hit, unresolved = set(), False, False
    target_path = Path(target).resolve()
    path_keys = {'source_path', 'run_dir', 'workspace_root', 'output_path', 'path',
                 'filename', 'file_path', 'directory', 'sandbox_dir'}

    def path_hit(value, base=None):
        nonlocal hit, unresolved
        if not isinstance(value, str) or not value:
            return
        path = Path(value)
        if not path.is_absolute() and base is None:
            return
        try:
            candidate = (path if path.is_absolute() else base / path).resolve()
            if candidate == target_path or candidate.is_relative_to(target_path):
                hit = True
        except (OSError, ValueError, RuntimeError):
            unresolved = True

    def walk(item):
        nonlocal hit
        if isinstance(item, dict):
            wid = item.get('workspace_id') or item.get('business_task_id')
            if wid == business_task_id and item.get('project', project) == project:
                hit = True
            if wid and item.get('project'):
                owners.add((str(item['project']), str(wid), str(item.get('business_task_id') or ''),
                            str(item.get('run_dir') or '')))
            base = Path(item['run_dir']) if item.get('run_dir') else (
                run_archive.PROJECT_ROOT / 'projects' / str(item['project']) / str(wid)
                if wid and item.get('project') else None)
            for key in path_keys & item.keys():
                path_hit(item[key], base)
            for child in item.values():
                walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            if target_text in item.replace('\\', '/').casefold():
                hit = True
            path_hit(item)
    walk(value)
    if hit:
        return 'target'
    if unresolved or not owners:
        return 'unknown'
    try:
        for owner_project, wid, business, directory in owners:
            path = Path(directory) if directory else run_archive.PROJECT_ROOT / 'projects' / owner_project / wid
            workspace._validate_run_identity(path, wid, owner_project, business)
    except (OSError, ValueError, workspace.WorkspaceError):
        return 'unknown'
    return 'foreign'


def request_value(record):
    if not isinstance(record, dict):
        raise ValueError('Invalid task record.')
    record = record.get('request', record)
    return [literal(record.get('args')), literal(record.get('kwargs'))]


def message_value(raw):
    if isinstance(raw, (str, bytes)):
        if len(raw) > LIMIT * 2:
            raise ValueError('Oversized broker record.')
        raw = json.loads(raw)
    if not isinstance(raw, dict) or raw.get('content-type') != 'application/json':
        raise ValueError('Only JSON broker messages can establish task ownership.')
    body, properties = raw.get('body'), raw.get('properties') or {}
    encoding = raw.get('content-encoding', 'utf-8')
    if properties.get('body_encoding') == 'base64':
        body = base64.b64decode(body, validate=True)
    elif isinstance(body, str):
        body = body.encode(encoding)
    if not isinstance(body, bytes):
        raise ValueError('Invalid broker body.')
    compression = (raw.get('headers') or {}).get('compression')
    if compression:
        if compression not in ('application/x-gzip', 'application/zlib', 'application/x-zlib'):
            raise ValueError('Unsupported compression.')
        decoder = zlib.decompressobj()
        body = decoder.decompress(body, LIMIT + 1)
        if len(body) > LIMIT or decoder.unconsumed_tail or not decoder.eof:
            raise ValueError('Unbounded compressed message.')
    if len(body) > LIMIT:
        raise ValueError('Oversized broker body.')
    return json.loads(body.decode(encoding))


def check_ttl(activity, now=None):
    current = time_now() if now is None else now
    start, end = activity.get('captured_at_epoch'), activity.get('expires_at_epoch')
    if (type(start) not in (int, float) or type(end) not in (int, float)
            or not math.isfinite(start) or not math.isfinite(end)
            or not start <= current < end <= start + MAX_ACTIVITY_LEASE_SEC):
        refuse('ACTIVITY_EXPIRED', 'Activity attestation is missing, future, expired or outside its 300-second lease.')
    if activity.get('unknown') != []:
        refuse('ACTIVITY_UNKNOWN', 'Activity attestation contains unresolved evidence.')


def time_now():
    import time
    return time.time()


def validate_activity(activity, project, business_task_id, target, ownership, configuration, *, now=None):
    check_ttl(activity, now)
    if (type(activity.get('schema_version')) is not int or activity.get('schema_version') != 1
            or activity.get('kind') != 'cgi_workspace_adoption_activity'
            or activity.get('target_project') != project or activity.get('business_task_id') != business_task_id
            or Path(str(activity.get('workspace_root', ''))).absolute() != target):
        refuse('IDENTITY_INVALID', 'Activity target identity does not match.')
    for key in ('sources', 'expected_nodes', 'known_task_ids', 'known_tasks', 'process_inventory'):
        if activity.get(key) != ownership.get(key):
            refuse('ACTIVITY_DRIFT', 'Activity ownership, source or terminal evidence changed.')
    if ownership.get('unknown'):
        refuse('ACTIVITY_UNKNOWN', 'Current process and ownership evidence is incomplete.')
    nodes = {row['node']: row for row in ownership['expected_nodes']}
    if any(row.get('role') == 'slot' and row.get('project') == project
           and row.get('workspace_id') == business_task_id for row in nodes.values()):
        refuse('ACTIVE_OR_UNKNOWN', 'A live target runtime prevents legacy workspace adoption.')
    replies = activity.get('replies')
    if not nodes or not isinstance(replies, dict) or set(replies) != set(REPLIES):
        refuse('ACTIVITY_UNKNOWN', 'Complete four-class inspect replies are required.')

    def clear(value):
        if classify(value, project, business_task_id, target) != 'foreign':
            refuse('ACTIVE_OR_UNKNOWN', 'Target or unowned activity prevents adoption.')

    for kind in REPLIES:
        rows = replies[kind]
        if not isinstance(rows, dict) or set(rows) != set(nodes):
            refuse('ACTIVITY_UNKNOWN', 'An expected worker did not reply.')
        for node, values in rows.items():
            if not isinstance(values, list):
                refuse('ACTIVITY_UNKNOWN', 'Invalid worker reply.')
            if kind == 'active_queues':
                if sorted(row.get('name') for row in values if isinstance(row, dict)) != nodes[node]['queues']:
                    refuse('ACTIVITY_UNKNOWN', 'Worker queue coverage changed.')
            else:
                for row in values:
                    try:
                        clear(request_value(row))
                    except (ValueError, TypeError, SyntaxError, RecursionError):
                        refuse('ACTIVITY_UNKNOWN', 'Task payload cannot be safely interpreted.')
    for records in (activity.get('ledger_activity'), ownership.get('ledger_activity')):
        slots = {row['runtime_id'] for row in ownership['expected_nodes'] if row['role'] == 'slot'}
        if (not isinstance(records, list) or len(records) != len(slots)
                or {row.get('runtime_id') for row in records} != slots):
            refuse('ACTIVITY_UNKNOWN', 'Incomplete ledger activity coverage.')
        for row in records:
            if row.get('active_task') or row.get('pending'):
                clear(row)
    broker = activity.get('broker', {})
    if (not isinstance(broker, dict) or broker.get('transport') != 'redis' or broker.get('identity') != configuration['identity']
            or broker.get('expected_queue_keys') != configuration['expected_queue_keys']):
        refuse('ACTIVITY_UNKNOWN', 'Broker identity or priority queue coverage is incomplete.')
    ready = broker.get('ready')
    if not isinstance(ready, dict) or set(ready) != set(configuration['expected_queue_keys']):
        refuse('ACTIVITY_UNKNOWN', 'Unknown or omitted broker list keys.')
    try:
        for records in ready.values():
            if not isinstance(records, list):
                raise ValueError()
            for raw in records:
                clear(message_value(raw))
        unacked, index = broker['unacked'], broker['unacked_index']
        if not isinstance(unacked, dict) or not isinstance(index, dict) or set(unacked) != set(index):
            raise ValueError()
        for tag, raw in unacked.items():
            if type(index[tag]) not in (int, float) or not math.isfinite(index[tag]):
                raise ValueError()
            packed = json.loads(raw)
            if not isinstance(packed, list) or len(packed) != 3:
                raise ValueError()
            clear(message_value(packed[0]))
    except (KeyError, ValueError, TypeError, RecursionError):
        refuse('ACTIVITY_UNKNOWN', 'Broker records cannot be safely interpreted.')
