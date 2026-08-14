"""CGI adapter for Blender Lab's official MCP addon transport."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from cgi_pipeline.contracts import ApiContractError, validate_receipt
from cgi_pipeline.paths import INTEGRATIONS_ROOT, REPOSITORY_ROOT, SOURCE_ROOT


PROJECT_ROOT = REPOSITORY_ROOT
AUDIT_DIR = PROJECT_ROOT / "audit"
UPSTREAM_MCP_ROOT = INTEGRATIONS_ROOT / "blender" / "extensions" / "blender_mcp" / "mcp"
HOST = "127.0.0.1"
_REQUEST_LOCK = threading.Lock()
_HEALTH_CODE = """import bpy, os
result = {
    'bridge': 'blender_mcp',
    'pid': os.getpid(),
    'blender_version': bpy.app.version_string,
    'file': bpy.data.filepath,
}
"""


class BlenderBridgeError(RuntimeError):
    def __init__(self, message: str, error_type: str = "BlenderBridgeError", response: dict[str, Any] | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.response = response or {}


def blender_port_range() -> range:
    start = int(os.getenv("BLENDER_FOREGROUND_PORT_START", "9876"))
    end = int(os.getenv("BLENDER_FOREGROUND_PORT_END", "9885"))
    if end < start:
        start, end = end, start
    return range(start, end + 1)


def _connection_module():
    if not UPSTREAM_MCP_ROOT.is_dir():
        raise BlenderBridgeError(
            f"Official Blender MCP support library is missing: {UPSTREAM_MCP_ROOT}",
            "UpstreamMissing",
        )
    upstream_path = str(UPSTREAM_MCP_ROOT)
    if upstream_path not in sys.path:
        sys.path.insert(0, upstream_path)
    try:
        from blmcp.tools_helpers import connection
    except ImportError as exc:
        raise BlenderBridgeError(str(exc), "UpstreamImportError") from exc
    return connection


def _send_code(port: int, code: str, timeout: float) -> dict[str, Any]:
    """Call the upstream client with an explicit port and bounded timeout."""
    connection = _connection_module()
    with _REQUEST_LOCK:
        old_host = os.environ.get("BLENDER_MCP_HOST")
        old_port = os.environ.get("BLENDER_MCP_PORT")
        old_timeout = connection._TIMEOUT
        os.environ["BLENDER_MCP_HOST"] = HOST
        os.environ["BLENDER_MCP_PORT"] = str(port)
        connection._TIMEOUT = max(float(timeout), 0.05)
        try:
            response = connection.send_code(code, strict_json=False)
        except ConnectionError as exc:
            raise BlenderBridgeError(str(exc), "ConnectionError") from exc
        finally:
            connection._TIMEOUT = old_timeout
            if old_host is None:
                os.environ.pop("BLENDER_MCP_HOST", None)
            else:
                os.environ["BLENDER_MCP_HOST"] = old_host
            if old_port is None:
                os.environ.pop("BLENDER_MCP_PORT", None)
            else:
                os.environ["BLENDER_MCP_PORT"] = old_port
    if not isinstance(response, dict):
        raise BlenderBridgeError("Official Blender MCP returned a non-object response", "InvalidResponse")
    return response


def request(port: int, code: str, timeout: float = 30.0) -> dict[str, Any]:
    """Execute Blender Python through the official addon transport."""
    return _send_code(int(port), code, timeout)


# The official Extension services an idle socket on a 1 s timer. Leave a small
# scheduling margin so a healthy foreground session is not reported as absent.
OFFICIAL_DISCOVERY_TIMEOUT = 1.5


def discover_blender_sessions(timeout: float = OFFICIAL_DISCOVERY_TIMEOUT) -> list[dict[str, Any]]:
    def probe(port: int) -> dict[str, Any] | None:
        try:
            response = request(port, _HEALTH_CODE, timeout=timeout)
        except BlenderBridgeError:
            return None
        result = response.get("result")
        if response.get("status") != "ok" or not isinstance(result, dict):
            return None
        if result.get("bridge") != "blender_mcp":
            return None
        return {**result, "port": port}

    ports = list(blender_port_range())
    with ThreadPoolExecutor(max_workers=len(ports)) as executor:
        sessions = [session for session in executor.map(probe, ports) if session]
    return sorted(sessions, key=lambda item: item["port"])


def _write_audit(task_id: str, status: str, api_id: str, detail: str) -> None:
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


def _resolve_port(context: dict[str, Any]) -> int:
    port = context.get("foreground_port")
    if port is not None:
        return int(port)
    sessions = discover_blender_sessions()
    if not sessions:
        raise BlenderBridgeError(
            "No active official Blender MCP addon found in ports 9876-9885",
            "NoForegroundSession",
        )
    if len(sessions) > 1:
        raise BlenderBridgeError(
            "Multiple Blender sessions found; foreground_port is required",
            "MultipleForegroundSessions",
            {"active_sessions": sessions},
        )
    return int(sessions[0]["port"])


def _api_code(payload: dict[str, Any], port: int) -> str:
    context = dict(payload.get("api_context") or {})
    context["execution_mode"] = "foreground"
    context["foreground_port"] = port
    return "\n".join((
        "import sys",
        f"source_root = {str(SOURCE_ROOT)!r}",
        "if source_root not in sys.path:",
        "    sys.path.insert(0, source_root)",
        "from cgi_pipeline.execution import execute_api",
        "result = execute_api(",
        f"    {payload.get('api_id', 'blender_exec_code')!r},",
        f"    params={payload.get('api_params') or {}!r},",
        f"    context={context!r},",
        ")",
    ))


def _traceback_details(message: str) -> tuple[str, str]:
    lines = [line for line in message.splitlines() if line.strip()]
    last_line = lines[-1] if lines else message
    error_type, separator, error = last_line.partition(":")
    return (error_type.strip() if separator else "BlenderPythonError", error.strip() if separator else last_line)


def _receipt_from_response(
    payload: dict[str, Any],
    port: int,
    response: dict[str, Any],
    duration_seconds: float,
) -> dict[str, Any]:
    api_id = str(payload.get("api_id") or "blender_exec_code")
    stdout = str(response.get("stdout") or "")
    stderr = str(response.get("stderr") or "")
    if response.get("status") != "ok":
        traceback_text = str(response.get("message") or "Official Blender MCP execution failed")
        error_type, error = _traceback_details(traceback_text)
        return {
            "api_id": api_id,
            "api_version": "1.0.0",
            "status": "ERROR",
            "input": dict(payload.get("api_params") or {}),
            "output": {"result": None},
            "elapsed_sec": duration_seconds,
            "error_code": "API_HOST_EXECUTION_ERROR",
            "error": error,
            "recovery_hint": "Inspect the Blender traceback and host session state.",
            "artifacts": [],
            "stdout": stdout,
            "stderr": stderr,
            "duration_seconds": duration_seconds,
            "error_type": error_type,
            "traceback": traceback_text,
            "foreground_port": port,
        }

    result = response.get("result")
    if isinstance(result, dict):
        try:
            receipt = validate_receipt(
                result,
                api_id=api_id,
                api_version=str(result.get("api_version") or "1.0.0"),
            )
        except ApiContractError as exc:
            return {
                "api_id": api_id,
                "api_version": str(result.get("api_version") or "1.0.0"),
                "status": "ERROR",
                "input": {},
                "output": {},
                "elapsed_sec": duration_seconds,
                "error_code": "API_INVALID_RECEIPT",
                "error": str(exc),
                "recovery_hint": "Blender host execution must return the canonical API receipt.",
                "artifacts": [],
                "stdout": stdout,
                "stderr": stderr,
                "foreground_port": port,
            }
        if receipt.get("status") == "ERROR" and not receipt.get("traceback"):
            receipt["traceback"] = receipt.get("recovery_hint", "")
        receipt["stdout"] = stdout
        receipt["stderr"] = stderr
        receipt["duration_seconds"] = duration_seconds
        receipt["foreground_port"] = port
        return receipt

    return {
        "api_id": api_id,
        "api_version": "1.0.0",
        "status": "ERROR",
        "input": {},
        "output": {},
        "elapsed_sec": duration_seconds,
        "error_code": "API_INVALID_RECEIPT",
        "error": "Blender host execution returned a non-object receipt.",
        "recovery_hint": "Blender host execution must return the canonical API receipt.",
        "artifacts": [],
        "stdout": stdout,
        "stderr": stderr,
        "duration_seconds": duration_seconds,
        "foreground_port": port,
    }


def submit_foreground_task(payload: dict[str, Any], sync: bool = False) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or f"task-{uuid.uuid4().hex[:12]}")
    api_id = str(payload.get("api_id") or "blender_exec_code")
    context = dict(payload.get("api_context") or {})
    try:
        port = _resolve_port(context)
        _write_audit(task_id, "STARTED", api_id, f"Official Blender MCP execution started on port {port}")
        started = time.perf_counter()
        response = request(
            port,
            _api_code(payload, port),
            float(context.get("timeout_seconds", 300.0)),
        )
        receipt = _receipt_from_response(payload, port, response, round(time.perf_counter() - started, 6))
        status = str(receipt["status"])
        _write_audit(task_id, status, api_id, json.dumps(receipt, ensure_ascii=False, default=repr))
    except BlenderBridgeError as exc:
        status = "SUBMIT_FAILED"
        receipt = {
            "api_id": api_id,
            "status": status,
            "error_type": exc.error_type,
            "error": str(exc),
            "recovery_hint": "Enable the official Blender MCP addon and pass foreground_port.",
        }
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
        "message": f"Task executed in Blender foreground session on port {receipt.get('foreground_port', context.get('foreground_port'))}",
        "execution_mode": "foreground",
    }
