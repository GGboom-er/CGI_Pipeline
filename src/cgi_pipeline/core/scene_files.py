"""Host-safe scene file boundaries, version names and cooperative OS locking.

Only standard-library imports are used. Keep the lock file after release: removing
it can let another process lock a different inode while existing waiters remain.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import re
import stat
import time


def is_managed_path(filepath: str | Path, repository_root: str | Path) -> bool:
    """Resolve links and traversal before checking the existing managed roots."""
    if not filepath:
        return False
    try:
        path, root = Path(filepath).resolve(), Path(repository_root).resolve()
        return path.is_relative_to(root / 'projects') or path.is_relative_to(root / 'runs')
    except (OSError, RuntimeError, ValueError):
        return False


def next_version_path(filepath: str) -> str:
    """Keep the established Maya naming rule; call while holding directory_lock."""
    path = Path(filepath)
    if not path.exists():
        return filepath
    match = re.search(r'(_?v)(\d+)$', path.stem, re.IGNORECASE)
    base = path.stem[:match.start()] if match else path.stem
    marker = match.group(1) if match else '_v'
    version = int(match.group(2)) if match else 0
    padding = len(match.group(2)) if match else 3
    candidate = path
    while candidate.exists():
        version += 1
        candidate = path.with_name(f'{base}{marker}{version:0{padding}d}{path.suffix}')
    return str(candidate.resolve())


def _set_lock(descriptor: int, *, release: bool = False) -> None:
    if os.name == 'nt':
        import msvcrt
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def directory_lock(directory: str | Path, *, timeout: float = 10):
    """Serialize version allocation and save; unavailable locks fail closed."""
    if timeout < 0:
        raise ValueError('Scene directory lock timeout must be non-negative')
    lock_path = Path(directory) / '.version_lock'
    if lock_path.is_symlink():
        raise ValueError(f'Scene directory lock cannot be a file link: {lock_path}')
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f'Scene directory lock must be a regular file: {lock_path}')
        deadline = time.monotonic() + timeout
        while True:
            try:
                _set_lock(descriptor)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'Scene directory .version_lock timeout: {lock_path}') from exc
                time.sleep(min(0.05, remaining))
        yield
    finally:
        try:
            if acquired:
                _set_lock(descriptor, release=True)
        finally:
            os.close(descriptor)
