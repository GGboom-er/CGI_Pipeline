"""CGI's loopback Blender GUI host; socket threads never access bpy.

Only the main-thread timer executes Python. A timed out request that has already
started cannot be interrupted; queued expired requests are never started.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import math
import os
import queue
import socket
import struct
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

HOST = "127.0.0.1"
MAGIC = b"CGI1"
MAX_FRAME = 16 * 1024 * 1024


class NativeBridgeError(RuntimeError):
    """A CGI host transport or lifecycle error; original cause is preserved."""


def _validate_port(port: int) -> int:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return port


def _read_exact(connection, count: int, deadline: float) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("CGI frame receive deadline expired")
        connection.settimeout(remaining)
        data = connection.recv(count - len(chunks))
        if not data:
            raise ConnectionError("CGI connection closed before the complete frame")
        chunks.extend(data)
    return bytes(chunks)


def _receive(connection, deadline: float) -> dict:
    header = _read_exact(connection, 8, deadline)
    if header[:4] != MAGIC:
        raise ValueError("Invalid CGI protocol magic")
    size = struct.unpack("!I", header[4:])[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("CGI frame size must be between 1 byte and 16 MiB")
    payload = json.loads(_read_exact(connection, size, deadline).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("CGI JSON payload must be an object")
    return payload


def _encode(message: dict) -> bytes:
    data = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if not 0 < len(data) <= MAX_FRAME:
        raise ValueError("CGI frame size exceeds 16 MiB")
    return MAGIC + struct.pack("!I", len(data)) + data


def send_request(port: int, message: dict, timeout: float) -> dict:
    """Perform one request with one deadline; never replay after a failure."""
    _validate_port(port)
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    if not isinstance(message, dict):
        raise ValueError("CGI request must be an object")
    payload = {**message, "deadline_unix": time.time() + timeout}
    deadline = time.monotonic() + timeout
    try:
        packet = _encode(payload)
        with socket.create_connection((HOST, port), timeout=timeout) as connection:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.sendall(packet)
            return _receive(connection, deadline)
    except (OSError, ValueError, TypeError, ConnectionError) as exc:
        raise NativeBridgeError(f"CGI Blender request on port {port} failed: {exc}") from exc


def _failure(exc: BaseException, stdout: str = "", stderr: str = "") -> dict:
    return {"status": "error", "message": "".join(traceback.format_exception(exc)),
            "stdout": stdout, "stderr": stderr}


@dataclass
class _Job:
    message: dict
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    response: dict | None = None
    cancelled: bool = False
    started: bool = False


class _Bridge:
    def __init__(self, bpy, listener):
        self.bpy, self.listener = bpy, listener
        self.port = listener.getsockname()[1]
        self.pending = queue.Queue(maxsize=128)
        self.closed = threading.Event()
        self.connections, self.lock = set(), threading.Lock()
        self.timer = self.tick  # Keep the exact callable identity for unregister.
        self.thread = threading.Thread(target=self.accept, name="CGI-Blender", daemon=True)

    def info(self) -> dict:
        return {"bridge": "cgi_pipeline", "protocol_version": 1, "dcc": "blender",
                "pid": os.getpid(), "blender_version": self.bpy.app.version_string,
                "file": getattr(self.bpy.data, "filepath", ""), "port": self.port}

    def tick(self):
        if threading.current_thread() is not threading.main_thread():
            raise NativeBridgeError("Blender GUI execution requires the main thread")
        if self.closed.is_set():
            return None
        for _ in range(4):
            try:
                job = self.pending.get_nowait()
            except queue.Empty:
                break
            with job.lock:
                if job.cancelled or time.time() >= job.deadline or self.closed.is_set():
                    job.response = _failure(TimeoutError("Request expired before execution"))
                    job.done.set()
                    continue
                job.started = True
            job.response = self.execute(job.message)
            job.done.set()
        return 0.05

    def execute(self, message: dict) -> dict:
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            if message["type"] == "health":
                result = self.info()
            else:
                namespace = {"__name__": "__cgi_foreground__", "bpy": self.bpy}
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exec(compile(message["code"], "<CGI Blender foreground>", "exec"), namespace)
                result = namespace.get("result")
            return {"status": "ok", "result": result,
                    "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}
        except BaseException as exc:
            return _failure(exc, stdout.getvalue(), stderr.getvalue())

    def accept(self):
        while not self.closed.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                if self.closed.is_set():
                    connection.close()
                    return
                self.connections.add(connection)
            threading.Thread(target=self.handle, args=(connection,), daemon=True).start()

    def handle(self, connection):
        try:
            message = _receive(connection, time.monotonic() + 2)
            if message.get("type") not in ("health", "execute"):
                raise ValueError("Unknown CGI request type")
            if message["type"] == "execute" and not isinstance(message.get("code"), str):
                raise ValueError("CGI execute code must be a string")
            deadline = float(message.get("deadline_unix", time.time() + 30))
            if not math.isfinite(deadline):
                raise ValueError("CGI request deadline must be finite")
            job = _Job(message, deadline)
            with self.lock:
                if self.closed.is_set():
                    raise NativeBridgeError("CGI Blender host stopped")
                self.pending.put_nowait(job)
            if not job.done.wait(max(0, deadline - time.time())):
                with job.lock:
                    job.cancelled = True
                raise TimeoutError("CGI request deadline expired; execution may already have started")
            response = job.response
            connection.settimeout(2)
            connection.sendall(_encode(response))
        except Exception as exc:
            try:
                connection.settimeout(1)
                connection.sendall(_encode(_failure(exc)))
            except OSError:
                pass
        finally:
            with self.lock:
                self.connections.discard(connection)
            connection.close()

    def stop(self):
        with self.lock:
            self.closed.set()
            self.listener.close()
            for connection in self.connections:
                connection.close()
        if self.bpy.app.timers.is_registered(self.timer):
            self.bpy.app.timers.unregister(self.timer)
        while True:
            try:
                job = self.pending.get_nowait()
            except queue.Empty:
                break
            job.cancelled = True
            job.response = _failure(NativeBridgeError("CGI Blender host stopped"))
            job.done.set()
        if self.thread.is_alive():
            self.thread.join(timeout=0.3)


# importlib.reload keeps this state and the registered timer callable intact.
if "_STATE" not in globals():
    _STATE: Any = None


def start(port: int | None = None) -> dict:
    """Start the GUI host, selecting a free local port when none is provided."""
    global _STATE
    if threading.current_thread() is not threading.main_thread():
        raise NativeBridgeError("CGI Blender host start requires the main thread")
    if port is not None:
        _validate_port(port)
    if _STATE is not None:
        if port is not None and port != _STATE.port:
            raise NativeBridgeError(f"CGI Blender host already running on {_STATE.port}; stop it first")
        return _STATE.info()
    bpy = importlib.import_module("bpy")
    if bpy.app.background:
        raise NativeBridgeError("CGI GUI host is unavailable in Blender background mode")
    if port is not None:
        candidates = [port]
    else:
        first = _validate_port(int(os.environ.get("BLENDER_FOREGROUND_PORT_START", "9876")))
        last = _validate_port(int(os.environ.get("BLENDER_FOREGROUND_PORT_END", "9885")))
        candidates = range(min(first, last), max(first, last) + 1)
    last_error = None
    for candidate in candidates:
        _validate_port(candidate)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind((HOST, candidate))
            listener.listen(16)
            listener.settimeout(0.2)
        except OSError as exc:
            listener.close()
            last_error = exc
            continue
        state = _Bridge(bpy, listener)
        try:
            bpy.app.timers.register(state.timer, first_interval=0.05, persistent=True)
            state.thread.start()
        except Exception:
            state.stop()
            raise
        _STATE = state
        return state.info()
    raise NativeBridgeError(f"Could not bind CGI Blender host to {list(candidates)}: {last_error}") from last_error


def stop() -> None:
    """Release listener, outstanding requests, and the exact registered timer."""
    global _STATE
    if _STATE is not None:
        if threading.current_thread() is not threading.main_thread():
            raise NativeBridgeError("CGI Blender host stop requires the main thread")
        _STATE.stop()
        _STATE = None
