"""Authenticated localhost bridge from CGI Pipeline to the current Blender UI."""

import contextlib
import hmac
import io
import json
import os
import queue
import secrets
import socket
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

import bpy
from bpy.app.handlers import persistent


HOST = "127.0.0.1"
PORT_START = 9876
PORT_END = 9885
MAX_REQUEST_BYTES = 4 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 300.0
TIMER_INTERVAL_SECONDS = 0.02

_server = None
_last_execution = {
    "status": "idle",
    "duration_seconds": None,
    "error": "",
}


def _token_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "CGI_Pipeline" / "blender_bridge.token"


def _load_or_create_token():
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    if token:
        return token

    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
        path.write_text(token, encoding="utf-8")
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token)
    return token


def _json_safe(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _execute_python(params):
    global _last_execution
    code = params.get("code", "")
    description = params.get("description", "")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("python.execute requires non-empty string parameter 'code'")

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    namespace = {
        "__builtins__": __builtins__,
        "__name__": "__cgi_pipeline_foreground__",
        "bpy": bpy,
        "result": None,
    }
    started = time.perf_counter()
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            exec(compile(code, f"<blender_exec_code: {description or 'foreground'}>", "exec"), namespace)
    except Exception as exc:
        duration = round(time.perf_counter() - started, 6)
        full_traceback = traceback.format_exc()
        _last_execution = {
            "status": "error",
            "duration_seconds": duration,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return {
            "status": "ERROR",
            "result": None,
            "stdout": stdout_buffer.getvalue(),
            "stderr": stderr_buffer.getvalue(),
            "duration_seconds": duration,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": full_traceback,
            "thread": threading.current_thread().name,
        }

    duration = round(time.perf_counter() - started, 6)
    _last_execution = {
        "status": "success",
        "duration_seconds": duration,
        "error": "",
    }
    return {
        "status": "SUCCESS",
        "result": _json_safe(namespace.get("result")),
        "stdout": stdout_buffer.getvalue(),
        "stderr": stderr_buffer.getvalue(),
        "duration_seconds": duration,
        "error_type": None,
        "error": None,
        "traceback": None,
        "thread": threading.current_thread().name,
    }


class BlenderBridgeServer:
    """Socket I/O stays on worker threads; bpy work is drained by a main-thread timer."""

    def __init__(self):
        self.session_id = uuid.uuid4().hex[:12]
        self.host = HOST
        self.port = None
        self._token = _load_or_create_token()
        self._socket = None
        self._accept_thread = None
        self._running = False
        self._requests = queue.Queue()
        self._timer_callback = self._drain_requests

    @property
    def healthy(self):
        return bool(
            self._running
            and self._socket is not None
            and self._accept_thread is not None
            and self._accept_thread.is_alive()
        )

    def start(self):
        if self.healthy:
            self.ensure_timer()
            return

        self.stop()
        for port in range(PORT_START, PORT_END + 1):
            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            candidate.settimeout(0.5)
            try:
                candidate.bind((self.host, port))
            except OSError:
                candidate.close()
                continue
            self._socket = candidate
            self.port = port
            break
        if self._socket is None:
            raise RuntimeError(f"No free Blender bridge port in {PORT_START}-{PORT_END}")

        self._socket.listen(8)
        self._running = True
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name=f"CGI-Blender-Bridge-{self.port}",
            daemon=True,
        )
        self._accept_thread.start()
        self.ensure_timer()
        print(f"[CGI Pipeline] Blender bridge listening on {self.host}:{self.port}")

    def stop(self):
        self._running = False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._accept_thread is not None and self._accept_thread is not threading.current_thread():
            self._accept_thread.join(timeout=1.5)
        self._accept_thread = None
        while True:
            try:
                queued = self._requests.get_nowait()
            except queue.Empty:
                break
            queued["response"] = self._error_response(
                queued.get("request", {}).get("id"),
                "BridgeStopped",
                "Blender bridge stopped before execution",
            )
            queued["event"].set()

    def ensure_timer(self):
        timers = bpy.app.timers
        try:
            if timers.is_registered(self._timer_callback):
                return
        except (AttributeError, RuntimeError):
            pass
        timers.register(self._timer_callback, first_interval=TIMER_INTERVAL_SECONDS, persistent=True)

    def _accept_loop(self):
        while self._running and self._socket is not None:
            try:
                connection, _address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                name=f"CGI-Blender-Client-{self.port}",
                daemon=True,
            ).start()

    def _handle_connection(self, connection):
        response = None
        request_id = None
        try:
            connection.settimeout(REQUEST_TIMEOUT_SECONDS)
            payload = b""
            while b"\n" not in payload:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                payload += chunk
                if len(payload) > MAX_REQUEST_BYTES:
                    raise ValueError("Bridge request exceeds size limit")
            request = json.loads(payload.split(b"\n", 1)[0].decode("utf-8"))
            request_id = request.get("id")
            queued = {"request": request, "response": None, "event": threading.Event()}
            self._requests.put(queued)
            if not queued["event"].wait(REQUEST_TIMEOUT_SECONDS):
                response = self._error_response(request_id, "BridgeTimeout", "Blender did not process the request in time")
            else:
                response = queued["response"]
        except Exception as exc:
            response = self._error_response(request_id, type(exc).__name__, str(exc))
        finally:
            if response is not None:
                try:
                    wire = json.dumps(response, ensure_ascii=False, default=repr).encode("utf-8") + b"\n"
                    connection.sendall(wire)
                except OSError:
                    pass
            connection.close()

    @staticmethod
    def _error_response(request_id, error_type, error, full_traceback=None):
        return {
            "id": request_id,
            "ok": False,
            "error_type": error_type,
            "error": error,
            "traceback": full_traceback,
        }

    def _drain_requests(self):
        while True:
            try:
                queued = self._requests.get_nowait()
            except queue.Empty:
                break
            queued["response"] = self._process_request(queued["request"])
            queued["event"].set()
        return TIMER_INTERVAL_SECONDS if self._running else None

    def _process_request(self, request):
        request_id = request.get("id")
        supplied_token = str(request.get("token", ""))
        if not supplied_token or not hmac.compare_digest(supplied_token, self._token):
            return self._error_response(request_id, "AuthenticationError", "Invalid Blender bridge token")

        method = request.get("method", "")
        params = request.get("params") or {}
        try:
            if method == "bridge.health":
                result = {
                    "status": "SUCCESS",
                    "bridge": "cgi_pipeline_blender_bridge",
                    "session_id": self.session_id,
                    "pid": os.getpid(),
                    "port": self.port,
                    "blender_version": bpy.app.version_string,
                    "file": bpy.data.filepath,
                    "thread": threading.current_thread().name,
                }
            elif method == "python.execute":
                result = _execute_python(params)
            else:
                raise ValueError(f"Unknown bridge method: {method}")
            return {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            return self._error_response(request_id, type(exc).__name__, str(exc), traceback.format_exc())


def _ensure_server():
    global _server
    if _server is None:
        _server = BlenderBridgeServer()
    _server.start()
    return None


@persistent
def _on_load_post(_unused):
    bpy.app.timers.register(_ensure_server, first_interval=0.1)


class CGI_PIPELINE_PT_bridge(bpy.types.Panel):
    bl_label = "CGI Pipeline Bridge"
    bl_idname = "CGI_PIPELINE_PT_bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CGI"

    def draw(self, _context):
        layout = self.layout
        if _server and _server.healthy:
            layout.label(text=f"Listening: {HOST}:{_server.port}", icon="LINKED")
        else:
            layout.label(text="Bridge stopped", icon="UNLINKED")
        layout.label(text=f"Last execution: {_last_execution['status']}")
        if _last_execution["duration_seconds"] is not None:
            layout.label(text=f"Duration: {_last_execution['duration_seconds']:.4f}s")
        if _last_execution["error"]:
            layout.label(text=_last_execution["error"][:80], icon="ERROR")


CLASSES = (CGI_PIPELINE_PT_bridge,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    _ensure_server()


def unregister():
    global _server
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if _server is not None:
        _server.stop()
        _server = None
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
