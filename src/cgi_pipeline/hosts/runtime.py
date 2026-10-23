"""Child-only runtime paths shared by host launchers and host-safe adapters.

Explicit module/plugin/script search sources stay shared. Isolated profiles start
with fresh preferences; existing profiles and projects are never copied. Host
thread settings are startup budgets, not an OS sandbox or a plugin CPU limit.
Keep imports host-safe: psutil is used only by the parent-side identity writer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class RuntimeNamespace:
    root: Path
    runtime_id: str = ''
    generation: str = ''
    threads: int | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.runtime_id)


def host_runtime(repository_root: str | Path,
                 environ: Mapping[str, str] | None = None) -> RuntimeNamespace:
    """Resolve supplied slot identity without changing environment or filesystem."""
    env = os.environ if environ is None else environ
    keys = ('CGI_RUNTIME_ID', 'CGI_RUNTIME_GENERATION', 'CGI_RUNTIME_ROOT')
    if not any(env.get(key, '').strip() for key in keys):
        return RuntimeNamespace(Path(repository_root).resolve())
    for key in keys:
        if not env.get(key, '').strip():
            raise ValueError(f'{key} is required for an isolated host runtime')
    root = Path(env['CGI_RUNTIME_ROOT'])
    if not root.is_absolute():
        raise ValueError('CGI_RUNTIME_ROOT must be absolute')
    threads = None
    if env.get('CGI_RUNTIME_THREADS', '').strip():
        try:
            threads = int(env['CGI_RUNTIME_THREADS'])
        except ValueError as exc:
            raise ValueError('CGI_RUNTIME_THREADS must be an integer from 1 to 1024') from exc
        if not 1 <= threads <= 1024:
            raise ValueError('CGI_RUNTIME_THREADS must be an integer from 1 to 1024')
    return RuntimeNamespace(root.resolve(), env['CGI_RUNTIME_ID'],
                            env['CGI_RUNTIME_GENERATION'], threads)


def _append_paths(existing: str, additions: list[str]) -> str:
    return os.pathsep.join(dict.fromkeys(
        path for path in [*existing.split(os.pathsep), *additions] if path))


def _shared_profile_paths(env: dict, host: str) -> None:
    if host == 'maya':
        previous = env.get('MAYA_APP_DIR')
        if previous:
            for key, folder in [('MAYA_MODULE_PATH', 'modules'),
                                ('MAYA_PLUG_IN_PATH', 'plug-ins'), ('MAYA_SCRIPT_PATH', 'scripts')]:
                env[key] = _append_paths(env.get(key, ''), [str(Path(previous) / folder)])
        return
    resources = env.get('BLENDER_USER_RESOURCES')
    for user, system, folder in [('BLENDER_USER_SCRIPTS', 'BLENDER_SYSTEM_SCRIPTS', 'scripts'),
                                  ('BLENDER_USER_EXTENSIONS', 'BLENDER_SYSTEM_EXTENSIONS', 'extensions')]:
        additions = [env[user]] if env.get(user) else []
        if resources:
            additions.append(str(Path(resources) / folder))
        if additions:
            env[system] = _append_paths(env.get(system, ''), additions)


def build_host_env(host: str, repository_root: str | Path,
                   environ: Mapping[str, str] | None = None) -> dict:
    """Build a new child environment and prepare only its writable slot folders."""
    if host not in ('maya', 'blender'):
        raise ValueError(f'Unsupported isolated host: {host}')
    env = dict(os.environ if environ is None else environ)
    runtime = host_runtime(repository_root, env)
    if not runtime.enabled:
        return env
    _shared_profile_paths(env, host)
    directories = {'TEMP': runtime.root / 'temp' / host, 'TMP': runtime.root / 'temp' / host}
    if host == 'maya':
        directories['MAYA_APP_DIR'] = runtime.root / 'maya'
    else:
        directories['BLENDER_USER_RESOURCES'] = runtime.root / 'blender'
        for key, folder in [('CONFIG', 'config'), ('DATAFILES', 'datafiles'),
                            ('SCRIPTS', 'scripts'), ('EXTENSIONS', 'extensions')]:
            directories['BLENDER_USER_' + key] = runtime.root / 'blender' / folder
    for key, directory in directories.items():
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    return env


def _host_record(runtime, worker_id, folder, value):
    if not worker_id or worker_id in ('.', '..') or any(char in worker_id for char in '/\\:\0'):
        raise ValueError('Host worker_id must be a single path segment')
    directory = runtime.root / folder
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{worker_id}.json'
    temporary = target.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def record_host_launch(runtime: RuntimeNamespace, host: str, worker_id: str):
    """Persist intent before Popen; an uncommitted launch stays UNKNOWN."""
    if runtime.enabled:
        return _host_record(runtime, worker_id, 'host_launches',
            {'runtime_id': runtime.runtime_id, 'generation': runtime.generation,
             'root': str(runtime.root), 'host': host, 'worker_id': worker_id, 'state': 'PENDING'})


def record_host_process(runtime: RuntimeNamespace, host: str, worker_id: str, process) -> Path | None:
    """Publish resource ownership; callers must stop their new host if this fails.

    The pre-Popen intent is committed only after exact ownership is published.
    """
    if not runtime.enabled:
        return None
    import psutil
    child = psutil.Process(process.pid)
    record = {'pid': process.pid, 'create_time': child.create_time(), 'cmdline': child.cmdline(),
              'runtime_id': runtime.runtime_id, 'generation': runtime.generation,
              'root': str(runtime.root), 'host': host, 'worker_id': worker_id}
    target = _host_record(runtime, worker_id, 'hosts', record)
    _host_record(runtime, worker_id, 'host_launches', {**record, 'state': 'COMMITTED'})
    return target
