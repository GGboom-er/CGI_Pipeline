"""Client for authenticated foreground execution in the current Blender UI."""

import json
import os
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = PROJECT_ROOT / "audit"
HOST = "127.0.0.1"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class BlenderBridgeError(RuntimeError):
    def __init__(self, message, error_type="BlenderBridgeError", response=None):
        super().__init__(message)
        self.error_type = error_type
        self.response = response or {}


def blender_port_range():
    start = int(os.getenv("BLENDER_FOREGROUND_PORT_START", "9876"))
    end = int(os.getenv("BLENDER_FOREGROUND_PORT_END", "9885"))
    if end < start:
        start, end = end, start
    return range(start, end + 1)


def token_path():
    override = os.getenv("BLENDER_BRIDGE_TOKEN_PATH")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "CGI_Pipeline" / "blender_bridge.token"


def read_token():
    path = token_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise BlenderBridgeError(
            f"Blender bridge token not found: {path}",
            "TokenNotFound",
        ) from exc
    if not token:
        raise BlenderBridgeError(f"Blender bridge token is empty: {path}", "TokenEmpty")
    return token


def request(port, method, params=None, token=None, timeout=30.0):
    request_id = uuid.uuid4().hex
    payload = {
        "id": request_id,
        "token": token if token is not None else read_token(),
        "method": method,
        "params": params or {},
    }
    try:
        with socket.create_connection((HOST, int(port)), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
            raw = b""
            while b"\n" not in raw:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                raw += chunk
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise BlenderBridgeError("Blender bridge response exceeds size limit", "ResponseTooLarge")
    except BlenderBridgeError:
        raise
    except (OSError, TimeoutError) as exc:
        raise BlenderBridgeError(str(exc), type(exc).__name__) from exc

    if not raw:
        raise BlenderBridgeError("Blender bridge closed without a response", "EmptyResponse")
    try:
        response = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlenderBridgeError(f"Invalid Blender bridge response: {exc}", "InvalidResponse") from exc
    if response.get("id") != request_id:
        raise BlenderBridgeError("Blender bridge response id mismatch", "ResponseMismatch", response)
    if not response.get("ok"):
        raise BlenderBridgeError(
            response.get("error", "Blender bridge request failed"),
            response.get("error_type", "BridgeRequestError"),
            response,
        )
    return response.get("result")


def discover_blender_sessions(timeout=0.25):
    try:
        token = read_token()
    except BlenderBridgeError:
        return []

    def probe(port):
        try:
            result = request(port, "bridge.health", token=token, timeout=timeout)
        except BlenderBridgeError:
            return None
        if not isinstance(result, dict) or result.get("bridge") != "cgi_pipeline_blender_bridge":
            return None
        result = dict(result)
        result["port"] = port
        return result

    ports = list(blender_port_range())
    with ThreadPoolExecutor(max_workers=len(ports)) as executor:
        sessions = [session for session in executor.map(probe, ports) if session]
    return sorted(sessions, key=lambda item: item["port"])


def _write_audit(task_id, status, api_id, detail):
    entry = {
        "task_id": task_id,
        "api_id": api_id,
        "status": status,
        "ts": time.time(),
        "detail": detail,
        "execution_mode": "foreground",
        "dcc": "blender",
    }
    date_dir = AUDIT_DIR / datetime.now().strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    with (date_dir / f"{task_id}.json").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=repr) + "\n")


def _resolve_port(parameters):
    port = parameters.get("foreground_port")
    if port is not None:
        return int(port)
    sessions = discover_blender_sessions()
    if not sessions:
        raise BlenderBridgeError(
            "No active Blender foreground bridge found in ports 9876-9885",
            "NoForegroundSession",
        )
    if len(sessions) > 1:
        raise BlenderBridgeError(
            "Multiple Blender sessions found; foreground_port is required",
            "MultipleForegroundSessions",
            {"active_sessions": sessions},
        )
    return int(sessions[0]["port"])


def submit_foreground_task(payload, sync=False):
    task_id = payload.get("task_id", f"task-{uuid.uuid4().hex[:12]}")
    api_id = payload.get("api_id", "blender_exec_code")
    parameters = payload.get("parameters", {})
    try:
        port = _resolve_port(parameters)
        _write_audit(task_id, "STARTED", api_id, f"Blender foreground execution started on port {port}")
        execution = request(
            port,
            "python.execute",
            {
                "code": parameters.get("code", ""),
                "description": parameters.get("description", ""),
            },
            timeout=float(parameters.get("timeout_seconds", 300.0)),
        )
        status = execution.get("status", "ERROR") if isinstance(execution, dict) else "ERROR"
        receipt = {
            "api_id": api_id,
            "status": status,
            "summary_action": parameters.get("description", ""),
            "outputs": {"result": execution.get("result")},
            "stdout": execution.get("stdout", ""),
            "stderr": execution.get("stderr", ""),
            "duration_seconds": execution.get("duration_seconds"),
            "error_type": execution.get("error_type"),
            "error": execution.get("error"),
            "traceback": execution.get("traceback"),
            "thread": execution.get("thread"),
            "foreground_port": port,
        }
        _write_audit(task_id, status, api_id, json.dumps(receipt, ensure_ascii=False, default=repr))
    except BlenderBridgeError as exc:
        status = "SUBMIT_FAILED"
        receipt = {
            "api_id": api_id,
            "status": status,
            "error_type": exc.error_type,
            "error": str(exc),
            "recovery_hint": "Open Blender with the CGI Pipeline Bridge enabled, discover sessions, then pass foreground_port.",
        }
        if exc.response:
            receipt.update(exc.response)
        _write_audit(task_id, status, api_id, json.dumps(receipt, ensure_ascii=False, default=repr))

    if sync:
        return {
            "task_id": task_id,
            "status": status,
            "api_id": api_id,
            "detail": receipt,
            "execution_mode": "foreground",
            "sync": True,
        }
    return {
        "task_id": task_id,
        "status": "SUBMITTED" if status != "SUBMIT_FAILED" else status,
        "api_id": api_id,
        "message": f"Task executed in Blender foreground session on port {receipt.get('foreground_port', parameters.get('foreground_port'))}",
        "execution_mode": "foreground",
    }
