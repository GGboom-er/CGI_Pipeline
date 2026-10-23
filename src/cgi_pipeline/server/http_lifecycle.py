"""Process-local admission and drain leases for the shared HTTP frontend."""

from __future__ import annotations

import asyncio
import contextvars
import math
import threading
import time
from contextlib import contextmanager


class LifecycleError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_current = contextvars.ContextVar("cgi_http_activity", default=None)


class _Activity:
    def __init__(self, owner, identifier: int):
        self.owner, self.identifier = owner, identifier

    @contextmanager
    def bind(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    def close(self):
        self.owner._release(self.identifier)


class HttpLifecycle:
    """Count actual frontend work, including threads surviving caller cancellation.

    Admission, lease transitions and stop commitment share one lock. An admitted
    activity may derive children during draining, but never after it is released.
    Celery execution after dispatch is outside this frontend's ownership.
    """

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._state = "accepting"
        self._epoch = 0
        self._deadline = None
        self._next_id = 0
        self._active = {}

    def _expire(self):
        if self._state == "draining" and self._clock() >= self._deadline:
            self._state, self._deadline = "accepting", None
            self._epoch += 1

    def _snapshot(self):
        return {
            "state": self._state,
            "active_count": len(self._active),
            "lease_epoch": self._epoch,
            "lease_remaining_seconds": (max(0.0, self._deadline - self._clock())
                                        if self._deadline is not None else 0.0),
        }

    def status(self):
        with self._lock:
            self._expire()
            return self._snapshot()

    def _acquire(self, label: str, *, inherit: bool):
        with self._lock:
            self._expire()
            parent = _current.get() if inherit else None
            derived = (parent is not None and parent.owner is self
                       and parent.identifier in self._active)
            if self._state != "accepting" and not derived:
                raise LifecycleError("HTTP_DRAINING", "Frontend is draining; retry after reconnecting.")
            self._next_id += 1
            self._active[self._next_id] = str(label)
            return _Activity(self, self._next_id)

    def _release(self, identifier):
        with self._lock:
            self._active.pop(identifier, None)

    @contextmanager
    def activity(self, label: str, *, inherit: bool = False):
        activity = self._acquire(label, inherit=inherit)
        try:
            with activity.bind():
                yield activity
        finally:
            activity.close()

    async def run_sync(self, function, /, *args, **kwargs):
        """Offload work without releasing its activity when the caller is cancelled."""
        activity = self._acquire(getattr(function, "__name__", "thread"), inherit=True)
        context = contextvars.copy_context()

        def invoke():
            try:
                with activity.bind():
                    return function(*args, **kwargs)
            finally:
                activity.close()

        try:
            future = asyncio.get_running_loop().run_in_executor(None, context.run, invoke)
        except BaseException:
            activity.close()
            raise

        def observe(done):
            if not done.cancelled():
                done.exception()  # Retrieve errors even when the awaiting client left.

        future.add_done_callback(observe)
        return await asyncio.shield(future)

    def spawn_thread(self, function, /, *args, daemon=True, name=None, **kwargs):
        """Register detached foreground work before starting its actual thread."""
        activity = self._acquire(getattr(function, "__name__", "thread"), inherit=True)
        context = contextvars.copy_context()

        def invoke():
            try:
                with activity.bind():
                    function(*args, **kwargs)
            finally:
                activity.close()

        try:
            thread = threading.Thread(target=context.run, args=(invoke,), daemon=daemon, name=name)
            thread.start()
        except BaseException:
            activity.close()
            raise
        return thread

    def _check_epoch(self, expected_epoch):
        if type(expected_epoch) is not int:
            raise LifecycleError("HTTP_CONTROL_INVALID", "lease_epoch must be an integer.")
        if expected_epoch != self._epoch:
            raise LifecycleError("HTTP_LEASE_CONFLICT", "Drain lease changed; read current status.")

    def drain(self, expected_epoch: int, lease_seconds: float):
        if (isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float))
                or not math.isfinite(lease_seconds) or not 1 <= lease_seconds <= 300):
            raise LifecycleError("HTTP_CONTROL_INVALID", "lease_seconds must be finite and between 1 and 300.")
        with self._lock:
            self._expire()
            self._check_epoch(expected_epoch)
            if self._state == "stopping":
                raise LifecycleError("HTTP_STOPPING", "Frontend already committed to exit.")
            if self._state == "accepting":
                self._epoch += 1
            self._state = "draining"
            self._deadline = self._clock() + lease_seconds
            return self._snapshot()

    def resume(self, expected_epoch: int):
        with self._lock:
            self._expire()
            self._check_epoch(expected_epoch)
            if self._state == "stopping":
                raise LifecycleError("HTTP_STOPPING", "Frontend already committed to exit.")
            self._state, self._deadline = "accepting", None
            self._epoch += 1
            return self._snapshot()

    def commit_stop(self, expected_epoch: int, on_stop):
        """Commit irreversibly only under a current drain lease with no owned work."""
        with self._lock:
            self._expire()
            self._check_epoch(expected_epoch)
            if self._state != "draining":
                raise LifecycleError("HTTP_NOT_DRAINING", "A current drain lease is required.")
            if self._active:
                raise LifecycleError("HTTP_ACTIVE_WORK", "Frontend still owns active work.")
            self._state, self._deadline = "stopping", None
            on_stop()
            return self._snapshot()


lifecycle = HttpLifecycle()
