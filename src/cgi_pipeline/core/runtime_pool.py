"""Atomic ownership of a finite set of process-isolated scene runtimes."""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path

from filelock import FileLock

from cgi_pipeline.contracts import ApiContractError
from cgi_pipeline.paths import REPOSITORY_ROOT


class RuntimePoolError(ApiContractError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.status = 'BLOCKED'
        self.dispatched = False


def _available_memory() -> int:
    import psutil
    return psutil.virtual_memory().available


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f'{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class RuntimePool:
    """Resource metadata only; task outcomes and scenes belong to workspaces."""

    def __init__(self, root=None, max_slots=None, *, min_free_gb=None, threads=None,
                 memory_probe=None, allow_external_root=False):
        self.root = Path(root or os.getenv('CGI_RUNTIME_POOL_ROOT') or
                         REPOSITORY_ROOT / 'runtime' / 'slots').resolve()
        managed = (REPOSITORY_ROOT / 'runtime', REPOSITORY_ROOT / 'projects')
        if not allow_external_root and not any(self.root.is_relative_to(path.resolve()) for path in managed):
            raise RuntimePoolError('RUNTIME_ROOT_INVALID', 'Runtime pool must remain under managed runtime or projects roots.')
        canonical = (REPOSITORY_ROOT / 'runtime' / 'slots').resolve()
        root_key = os.path.normcase(str(self.root))
        self.namespace = ('' if root_key == os.path.normcase(str(canonical)) else
                          hashlib.sha256(root_key.encode('utf-8')).hexdigest()[:12])
        self.max_slots = int(max_slots if max_slots is not None else
                             os.getenv('CGI_RUNTIME_MAX_SLOTS', '3'))
        self.min_free_gb = float(min_free_gb if min_free_gb is not None else
                                 os.getenv('CGI_RUNTIME_MIN_FREE_GB', '8'))
        self.threads = int(threads if threads is not None else
                           os.getenv('CGI_RUNTIME_THREADS', '6'))
        if (self.max_slots < 1 or self.threads < 1 or self.min_free_gb < 0
                or not math.isfinite(self.min_free_gb)):
            raise ValueError('Invalid runtime slot, memory reserve or thread configuration.')
        self.memory_probe = memory_probe or _available_memory
        self.path = self.root / 'runtime_pool.json'

    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or (self.root / 'allocation.lock').is_symlink():
            raise RuntimePoolError('RUNTIME_STATE_INVALID', 'Runtime metadata cannot be a link.')
        return FileLock(str(self.root / 'allocation.lock'), timeout=30)

    def _read(self):
        if not self.path.exists():
            return {'version': 1, 'slots': {}, 'lost': {}}
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if (value.get('version') != 1 or not isinstance(value.get('slots'), dict)
                    or not isinstance(value.get('lost'), dict)):
                raise ValueError('Unsupported runtime ledger.')
            return value
        except (OSError, ValueError, AttributeError) as exc:
            raise RuntimePoolError('RUNTIME_STATE_INVALID', str(exc)) from exc

    @staticmethod
    def _key(project, workspace_id):
        if (not isinstance(project, str) or not project or project in ('.', '..')
                or any(c in project for c in '/\\:')):
            raise RuntimePoolError('RUNTIME_BINDING_INVALID', 'Invalid project key.')
        if not isinstance(workspace_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', workspace_id):
            raise RuntimePoolError('RUNTIME_BINDING_INVALID', 'Invalid workspace identity.')
        return json.dumps([project, workspace_id])

    @staticmethod
    def _match(row, lease):
        fields = ('runtime_id', 'generation', 'queue', 'root', 'project', 'workspace_id')
        return isinstance(lease, dict) and all(row.get(k) == lease.get(k) for k in fields)

    @staticmethod
    def _match_worker(row, identity):
        fields = ('pid', 'create_time', 'cmdline', 'runtime_id', 'generation', 'root')
        return (isinstance(identity, dict) and isinstance(row.get('worker'), dict)
                and type(identity.get('pid')) is int
                and isinstance(identity.get('create_time'), (int, float))
                and math.isfinite(identity['create_time'])
                and isinstance(identity.get('cmdline'), list) and bool(identity['cmdline'])
                and all(isinstance(part, str) for part in identity['cmdline'])
                and all(field in identity and row['worker'].get(field) == identity[field] for field in fields))

    def _validate(self, ledger, lease):
        row = ledger['slots'].get(lease.get('runtime_id')) if isinstance(lease, dict) else None
        if row is None or not self._match(row, lease):
            raise RuntimePoolError('RUNTIME_GENERATION_MISMATCH', 'Runtime assignment is stale or foreign.')
        return row

    def _change(self, lease, update):
        with self._lock():
            ledger = self._read()
            row = self._validate(ledger, lease)
            update(row, ledger)
            row['updated_at'] = time.time()
            _write_json(self.path, ledger)
            return deepcopy(row)

    def acquire(self, project, workspace_id, *, rebuild=False):
        key = self._key(project, workspace_id)
        with self._lock():
            ledger = self._read()
            for row in ledger['slots'].values():
                if (row.get('lifecycle', 'resident') == 'resident'
                        and (row['project'], row['workspace_id']) == (project, workspace_id)):
                    if row['state'] == 'DRAINING':
                        raise RuntimePoolError('RUNTIME_DRAINING', 'Workspace runtime is draining.')
                    return deepcopy(row)
            if key in ledger['lost'] and not rebuild:
                raise RuntimePoolError('RUNTIME_LOST', 'Scene runtime was lost; use an explicit open to rebuild.')
            runtime_id = next((f'slot-{i}' for i in range(1, self.max_slots + 1)
                               if f'slot-{i}' not in ledger['slots']), None)
            residents = [row for row in ledger['slots'].values()
                         if row.get('lifecycle', 'resident') == 'resident']
            if runtime_id is None or len(residents) >= self.max_slots:
                raise RuntimePoolError('RUNTIME_CAPACITY_BUSY', 'All runtime slots are bound; no message was dispatched.')
            if self.memory_probe() < self.min_free_gb * 1024 ** 3:
                raise RuntimePoolError('RUNTIME_MEMORY_LOW', 'Available physical memory is below the configured reserve.')
            now = time.time()
            row = dict(runtime_id=runtime_id, generation=uuid.uuid4().hex,
                       queue=f'cgi_{runtime_id.replace("-", "_")}' +
                       (f'_{self.namespace}' if self.namespace else ''),
                       root=str(self.root / runtime_id), project=project,
                       workspace_id=workspace_id, state='STARTING', pending={}, active_task='',
                       worker=None, threads=self.threads, host_publication_version=1,
                       created_at=now, updated_at=now)
            ledger['slots'][runtime_id] = row
            _write_json(self.path, ledger)
            return deepcopy(row)

    def validate(self, lease):
        with self._lock():
            return deepcopy(self._validate(self._read(), lease))

    def acquire_workflow(self, project, workspace_id, task_id):
        """An operation-owned lane; never borrow a resident's scene or quota."""
        self._key(project, workspace_id)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', task_id):
            raise RuntimePoolError('RUNTIME_TASK_INVALID', 'Invalid workflow operation identity.')
        with self._lock():
            ledger = self._read()
            for row in ledger['slots'].values():
                if (row.get('lifecycle') == 'workflow' and row.get('operation_id') == task_id
                        and (row['project'], row['workspace_id']) == (project, workspace_id)):
                    return deepcopy(row)
            # Charge a reservation before launch so simultaneous cold starts
            # cannot all spend the same currently available physical memory.
            budget = float(os.getenv('CGI_WORKFLOW_MEMORY_GB', '4'))
            if not math.isfinite(budget) or budget <= 0:
                raise ValueError('Workflow memory reservation must be positive and finite.')
            reserved = sum(row.get('memory_reservation_gb', 0)
                           for row in ledger['slots'].values()
                           if row.get('lifecycle') == 'workflow')
            if self.memory_probe() < (self.min_free_gb + reserved + budget) * 1024 ** 3:
                raise RuntimePoolError('RUNTIME_MEMORY_LOW', 'Workflow waits for physical memory reserve.')
            runtime_id = 'workflow-' + uuid.uuid4().hex
            now = time.time()
            row = dict(runtime_id=runtime_id, generation=uuid.uuid4().hex,
                       queue='cgi_' + runtime_id.replace('-', '_') +
                             (f'_{self.namespace}' if self.namespace else ''),
                       root=str(self.root / runtime_id), project=project,
                       workspace_id=workspace_id, operation_id=task_id, lifecycle='workflow',
                       memory_reservation_gb=budget, state='STARTING', pending={},
                       active_task='', worker=None, threads=self.threads,
                       host_publication_version=1, created_at=now, updated_at=now)
            ledger['slots'][runtime_id] = row
            _write_json(self.path, ledger)
            return deepcopy(row)

    def reserve(self, lease, task_id):
        if not isinstance(task_id, str) or not task_id:
            raise RuntimePoolError('RUNTIME_TASK_INVALID', 'A task identity is required.')

        def update(row, ledger):
            if row.get('lifecycle') == 'workflow' and row.get('operation_id') != task_id:
                raise RuntimePoolError('RUNTIME_TASK_INVALID', 'Temporary runtime belongs to another operation.')
            if row['state'] not in ('IDLE', 'BUSY'):
                raise RuntimePoolError('RUNTIME_UNAVAILABLE', 'Runtime is not accepting messages.')
            if row['active_task'] != task_id:
                row['pending'].setdefault(task_id, time.time())
        return self._change(lease, update)

    def unreserve(self, lease, task_id, *, worker=None):
        if not isinstance(task_id, str) or not task_id:
            raise RuntimePoolError('RUNTIME_TASK_INVALID', 'A task identity is required.')
        with self._lock():
            ledger = self._read()
            row = self._validate(ledger, lease)
            if worker is not None and not self._match_worker(row, worker):
                raise RuntimePoolError('RUNTIME_PROCESS_MISMATCH', 'Reservation worker identity does not match.')
            if row['active_task'] == task_id:
                raise RuntimePoolError('RUNTIME_BUSY', 'An active task cannot release a pending reservation.')
            if task_id in row['pending']:
                row['pending'].pop(task_id)
                row['updated_at'] = time.time()
                _write_json(self.path, ledger)
            return deepcopy(row)

    def set_busy(self, lease, task_id):
        def update(row, ledger):
            if row['state'] not in ('IDLE', 'BUSY') or row['active_task'] not in ('', task_id):
                raise RuntimePoolError('RUNTIME_BUSY', 'Runtime has another active owner or is draining.')
            if row['active_task'] != task_id and task_id not in row['pending']:
                raise RuntimePoolError('RUNTIME_TASK_NOT_RESERVED', 'Task has no accepted resource reservation.')
            row['pending'].pop(task_id, None)
            row.update(state='BUSY', active_task=task_id)
        return self._change(lease, update)

    def set_idle(self, lease, task_id):
        def update(row, ledger):
            if row['active_task'] != task_id:
                raise RuntimePoolError('RUNTIME_BUSY', 'Only the active task can release execution ownership.')
            row.update(state='IDLE', active_task='')
        return self._change(lease, update)

    def set_worker_info(self, lease, identity):
        def update(row, ledger):
            if row['state'] in ('IDLE', 'BUSY') and row['worker'] == identity:
                return
            if row['state'] != 'STARTING' or row['worker'] is not None:
                raise RuntimePoolError('RUNTIME_BUSY', 'Runtime worker ownership is already established.')
            if any(identity.get(k) != row[k] for k in ('runtime_id', 'generation', 'root')):
                raise RuntimePoolError('RUNTIME_GENERATION_MISMATCH', 'Worker identity does not match its lease.')
            row.update(worker=deepcopy(identity), state='IDLE')
            if row.get('lifecycle') != 'workflow':
                ledger['lost'].pop(self._key(row['project'], row['workspace_id']), None)
        return self._change(lease, update)

    def begin_release(self, lease):
        def update(row, ledger):
            if row['active_task'] or row['pending'] or row['state'] not in ('IDLE', 'DRAINING'):
                raise RuntimePoolError('RUNTIME_BUSY', 'Accepted or active work prevents release.')
            if row['state'] != 'DRAINING':
                row['release_state'] = row['state']
                row['state'] = 'DRAINING'
        return self._change(lease, update)

    def finish_release(self, lease, released: bool):
        def update(row, ledger):
            if row['state'] != 'DRAINING':
                raise RuntimePoolError('RUNTIME_RELEASE_INVALID', 'Runtime has not begun draining.')
            previous = row.pop('release_state', 'IDLE')
            if released:
                ledger['slots'].pop(row['runtime_id'])
                row['state'] = 'RELEASED'
            else:
                row['state'] = previous
        return self._change(lease, update)

    def begin_recovery(self, lease, *, explicit_discard, confirmed_worker_dead):
        if explicit_discard is not True or confirmed_worker_dead is not True:
            raise RuntimePoolError('RUNTIME_RECOVERY_UNCONFIRMED',
                                   'Recovery requires explicit discard and exact confirmed worker death.')

        def update(row, ledger):
            abandoned = row.setdefault('abandoned_tasks', {})
            for task_id, reserved_at in row['pending'].items():
                abandoned.setdefault(task_id, {'state': 'PENDING', 'reserved_at': reserved_at})
            if row['active_task']:
                abandoned.setdefault(row['active_task'], {'state': 'ACTIVE', 'abandoned_at': time.time()})
            row.update(state='DRAINING', active_task='', pending={},
                       publication_unknown='Worker died; complete host launch publication must be confirmed before freeing this slot.')
            row.pop('release_state', None)
        return self._change(lease, update)

    def rollback(self, lease):
        def update(row, ledger):
            if row['state'] != 'STARTING' or row['worker'] or row['pending'] or row['active_task']:
                raise RuntimePoolError('RUNTIME_ROLLBACK_UNSAFE', 'Only an unstarted assignment can be rolled back.')
            ledger['slots'].pop(row['runtime_id'])
            row['state'] = 'RELEASED'
        return self._change(lease, update)

    def mark_lost(self, lease, *, confirmed_dead=False):
        if confirmed_dead is not True:
            raise RuntimePoolError('RUNTIME_DEATH_UNCONFIRMED', 'Process death must be confirmed before freeing resources.')

        def update(row, ledger):
            row.update(state='LOST', lost_at=time.time())
            key = (row['operation_id'] if row.get('lifecycle') == 'workflow'
                   else self._key(row['project'], row['workspace_id']))
            ledger['lost'][key] = row
            ledger['slots'].pop(row['runtime_id'])
        return self._change(lease, update)

    def status(self, project='', workspace_id=''):
        with self._lock():
            ledger = self._read()
            rows = [*ledger['slots'].values(), *ledger['lost'].values()]
            return deepcopy([row for row in rows if (not project or row['project'] == project)
                             and (not workspace_id or row['workspace_id'] == workspace_id)])
