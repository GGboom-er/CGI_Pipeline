"""Lifecycle of one leased Celery worker and its verified owned host tree."""
from __future__ import annotations

import os
import json
import socket
import sys
import time
from pathlib import Path

import psutil
from filelock import FileLock

from cgi_pipeline.core import service_manager as services
from cgi_pipeline.core.runtime_pool import RuntimePool, RuntimePoolError, _write_json
from cgi_pipeline.core.runtime_state import (
    owned_inventory, process_state, read_identity, snapshot, publication_complete,
)
from cgi_pipeline.paths import REPOSITORY_ROOT, SOURCE_ROOT


class RuntimeService:
    def __init__(self, pool=None, *, spawn=None, kill_tree=None, process_factory=None,
                 redis_start=None, queue_probe=None, timeout_sec=30, poll_interval=0.1):
        self.pool = pool or RuntimePool()
        self.spawn = spawn or services._spawn_shared_process
        self.kill_tree = kill_tree or services._kill_process_tree
        self.process_factory = process_factory or psutil.Process
        self.redis_start = redis_start or services.start_redis
        self.queue_probe = queue_probe or self._queue_ready
        self.timeout_sec = timeout_sec
        self.poll_interval = poll_interval

    @staticmethod
    def _node(lease):
        return f'{lease["queue"].replace("_", "-")}@{socket.gethostname()}'

    @staticmethod
    def _queue_ready(lease, identity):
        inspector = services.get_celery_app().control.inspect(
            destination=[identity['node']], timeout=1)
        queues = inspector.active_queues() or {}
        return any(item.get('name') == lease['queue'] for item in queues.get(identity['node'], []))

    @staticmethod
    def _worker(lease):
        if lease.get('worker'):
            return lease['worker']
        path = Path(lease['root']) / 'owner.json'
        return read_identity(path, lease) if path.exists() else None

    def _environment(self, lease):
        root = Path(lease['root'])
        for name in ('ipc', 'logs', 'temp'):
            (root / name).mkdir(parents=True, exist_ok=True)
        env = services._managed_python_env()
        env.update(CGI_RUNTIME_ID=lease['runtime_id'], CGI_RUNTIME_GENERATION=lease['generation'],
                   CGI_RUNTIME_ROOT=lease['root'], CGI_RUNTIME_POOL_ROOT=str(self.pool.root),
                   CGI_RUNTIME_THREADS=str(lease['threads']), PYTHONUNBUFFERED='1',
                   TEMP=str(root / 'temp'), TMP=str(root / 'temp'))
        for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
            env[key] = str(lease['threads'])
        paths = [str(SOURCE_ROOT), *env.get('PYTHONPATH', '').split(os.pathsep)]
        env['PYTHONPATH'] = os.pathsep.join(dict.fromkeys(item for item in paths if item))
        return env

    def _command(self, lease):
        root = Path(lease['root'])
        return [sys.executable, '-m', 'celery', '-A', 'cgi_pipeline.core.tasks', 'worker',
                '-Q', lease['queue'], '--pool=solo', '-c', '1', '-l', 'info',
                f'--hostname={self._node(lease)}', f'--pidfile={root / ("worker-" + lease["generation"] + ".pid")}',
                f'--logfile={root / "logs" / "worker.log"}']

    @staticmethod
    def _launch_pending(lease):
        path = Path(lease['root']) / 'launch.json'
        if not path.exists():
            return False
        try:
            if path.is_symlink():
                raise ValueError('Launch metadata cannot be a link.')
            value = json.loads(path.read_text(encoding='utf-8'))
            return value['generation'] == lease['generation']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimePoolError('RUNTIME_IDENTITY_UNKNOWN', str(exc)) from exc

    def _confirm_queue(self, lease, identity):
        deadline = time.monotonic() + self.timeout_sec
        while True:
            if self.queue_probe(lease, identity):
                if process_state(identity, lease, self.process_factory) != 'LIVE':
                    raise RuntimePoolError('RUNTIME_START_UNCONFIRMED', 'Queue responded but its exact worker is not live.')
                self.pool.set_worker_info(lease, identity)
                return identity
            if time.monotonic() >= deadline or process_state(identity, lease, self.process_factory) != 'LIVE':
                raise RuntimePoolError('RUNTIME_START_FAILED', 'Worker did not confirm its assigned queue.')
            time.sleep(self.poll_interval)

    def health(self, lease):
        row = self.pool.validate(lease)
        result = {'runtime_id': row['runtime_id'], 'generation': row['generation'],
                  'checked_at': time.time(), 'lease_state': row['state'],
                  'confirmed_dead': False, 'pid_alive': False, 'dirty_state': 'UNKNOWN'}
        try:
            worker = self._worker(row)
            if worker is None:
                if self._launch_pending(row):
                    return {**result, 'state': 'UNKNOWN', 'error': 'Launch outcome has no process identity.'}
                return {**result, 'state': 'STARTING', 'worker': None}
            records = owned_inventory(row, worker, self.process_factory)
            states = [process_state(item, row, self.process_factory) for item in records]
            root_state = process_state(worker, row, self.process_factory)
            result.update(worker=worker, pid_alive=root_state == 'LIVE', owned_processes=records)
            if 'FOREIGN' in states:
                return {**result, 'state': 'IDENTITY_MISMATCH'}
            if root_state == 'LIVE':
                return {**result, 'state': 'LIVE'}
            if 'LIVE' in states:
                return {**result, 'state': 'ORPHANED'}
            complete, reason = publication_complete(row)
            if row['active_task'] and not complete:
                return {**result, 'state': 'UNKNOWN', 'error': 'Active owner died; host publication may be incomplete.'}
            if not complete and row.get('host_publication_version') == 1:
                return {**result, 'state': 'UNKNOWN', 'error': reason}
            return {**result, 'state': 'DEAD', 'confirmed_dead': True}
        except RuntimePoolError as exc:
            return {**result, 'state': 'UNKNOWN', 'error': str(exc), 'error_code': exc.code}

    def _stop_owned(self, lease, worker):
        records = owned_inventory(lease, worker, self.process_factory)
        if any(process_state(item, lease, self.process_factory) == 'FOREIGN' for item in records):
            return False
        for item in records:
            state = process_state(item, lease, self.process_factory)
            if state == 'FOREIGN':
                return False
            if state == 'LIVE':
                self.kill_tree(item['pid'], timeout=5)
        return all(process_state(item, lease, self.process_factory) == 'DEAD' for item in records)

    def stop(self, lease):
        row = self.pool.validate(lease)
        if row['state'] != 'DRAINING' or row['active_task'] or row['pending']:
            raise RuntimePoolError('RUNTIME_BUSY', 'Stopping requires a drained workspace and generation.')
        try:
            if row.get('host_publication_version') == 1 and not publication_complete(row)[0]:
                return False
            return self._stop_owned(row, self._worker(row))
        except RuntimePoolError:
            return False

    def ensure(self, lease):
        row = self.pool.validate(lease)
        root = Path(row['root'])
        root.mkdir(parents=True, exist_ok=True)
        if (root / 'start.lock').is_symlink():
            raise RuntimePoolError('RUNTIME_IDENTITY_UNKNOWN', 'Runtime start lock cannot be a link.')
        with FileLock(str(root / 'start.lock'), timeout=self.timeout_sec + 5):
            row = self.pool.validate(lease)
            if row['state'] == 'DRAINING':
                raise RuntimePoolError('RUNTIME_DRAINING', 'Runtime is draining.')
            worker = self._worker(row)
            if worker is not None:
                health = self.health(row)
                if health['state'] == 'LIVE':
                    if row['state'] == 'STARTING':
                        return self._confirm_queue(row, worker)
                    return worker
                raise RuntimePoolError('RUNTIME_LOST', f'Worker cannot be reused: {health["state"]}.')
            if row['state'] != 'STARTING':
                raise RuntimePoolError('RUNTIME_IDENTITY_UNKNOWN', 'Assigned runtime has no worker identity.')
            if self._launch_pending(row):
                raise RuntimePoolError('RUNTIME_START_UNCONFIRMED', 'Previous launch outcome is unknown; refusing a duplicate worker.')
            return self._start(row)

    def _start(self, lease):
        identity = None
        spawn_attempted = False
        try:
            if not self.redis_start():
                raise RuntimePoolError('RUNTIME_START_FAILED', 'Shared broker is unavailable.')
            _write_json(Path(lease['root']) / 'launch.json',
                        {key: lease[key] for key in ('runtime_id', 'generation', 'root')})
            spawn_attempted = True
            process = self.spawn(self._command(lease), cwd=REPOSITORY_ROOT,
                                 env=self._environment(lease))
            identity = snapshot(process.pid, self.process_factory)
            if identity is None:
                raise RuntimePoolError('RUNTIME_START_FAILED', 'Worker exited before ownership was recorded.')
            identity.update(node=self._node(lease), started_at=time.time())
            if any(identity.get(k) != lease[k] for k in ('runtime_id', 'generation', 'root')):
                raise RuntimePoolError('RUNTIME_START_UNCONFIRMED', 'Spawned worker ownership cannot be verified.')
            _write_json(Path(lease['root']) / 'owner.json', identity)
            return self._confirm_queue(lease, identity)
        except Exception as exc:
            # A failure after launch must not free a resource whose death is unknown.
            if isinstance(exc, RuntimePoolError) and exc.code == 'RUNTIME_IDENTITY_UNKNOWN':
                raise
            if spawn_attempted and identity is None:
                raise RuntimePoolError('RUNTIME_START_UNCONFIRMED', 'Launch outcome has no confirmed process identity; assignment retained.') from exc
            if isinstance(exc, (psutil.AccessDenied, PermissionError)):
                raise RuntimePoolError('RUNTIME_START_UNCONFIRMED', 'Process inspection or launch outcome is unknown.') from exc
            stopped = identity is None or self._stop_owned(lease, identity)
            if not stopped:
                raise RuntimePoolError('RUNTIME_START_UNCONFIRMED', 'Startup failed; owned processes are not confirmed stopped.') from exc
            self.pool.rollback(lease)
            if isinstance(exc, RuntimePoolError):
                raise
            raise RuntimePoolError('RUNTIME_START_FAILED', str(exc)) from exc
