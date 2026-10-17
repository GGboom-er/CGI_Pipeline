"""Foreground API client for CGI's native Blender bridge."""

from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from cgi_pipeline.contracts import ApiContractError, validate_receipt
from cgi_pipeline.hosts.blender import native_bridge
from cgi_pipeline.paths import REPOSITORY_ROOT, SOURCE_ROOT


PROJECT_ROOT = REPOSITORY_ROOT
AUDIT_DIR = PROJECT_ROOT / "audit"
DEFAULT_DISCOVERY_TIMEOUT = 0.5


class BlenderBridgeError(RuntimeError):
    def __init__(self, message: str, error_type: str = "BlenderBridgeError", response: dict[str, Any] | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.response = response or {}


def _port(value: Any) -> int:
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError
        port = int(value)
        if not 1 <= port <= 65535:
            raise ValueError
        return port
    except (TypeError, ValueError) as exc:
        raise BlenderBridgeError(f"Invalid Blender foreground port: {value!r}", "InvalidPort") from exc


def blender_port_range() -> range:
    start = _port(os.getenv("BLENDER_FOREGROUND_PORT_START", "9876"))
    end = _port(os.getenv("BLENDER_FOREGROUND_PORT_END", "9885"))
    if end < start:
        start, end = end, start
    return range(start, end + 1)


def blender_ports() -> list[int]:
    """Scan the configured range plus explicitly registered custom ports."""
    extra = os.getenv("BLENDER_FOREGROUND_PORTS", "")
    ports = set(blender_port_range())
    ports.update(_port(value.strip()) for value in extra.split(",") if value.strip())
    return sorted(ports)


def _send_message(port: int, message: dict[str, Any], timeout: float) -> dict[str, Any]:
    try:
        response = native_bridge.send_request(port, message, timeout=timeout)
    except (native_bridge.NativeBridgeError, OSError, ValueError) as exc:
        raise BlenderBridgeError(str(exc), "ConnectionError") from exc
    if not isinstance(response, dict):
        raise BlenderBridgeError("CGI Blender bridge returned a non-object response", "InvalidResponse")
    return response


def _health(port: int, timeout: float) -> dict[str, Any]:
    response = _send_message(port, {"type": "health"}, timeout)
    result = response.get("result")
    if (response.get("status") != "ok" or not isinstance(result, dict)
            or result.get("bridge") != "cgi_pipeline"
            or result.get("protocol_version") != 1 or result.get("dcc") != "blender"):
        raise BlenderBridgeError(f"Port {port} is not a compatible CGI Blender bridge", "IncompatibleBridge", response)
    return {**result, "port": port}


def request(port: int, code: str, timeout: float = 30.0) -> dict[str, Any]:
    """Confirm CGI identity without executing code before submitting Python."""
    port = _port(port)
    _health(port, min(float(timeout), DEFAULT_DISCOVERY_TIMEOUT))
    return _send_message(port, {"type": "execute", "code": code}, timeout)


def discover_blender_sessions(timeout: float = DEFAULT_DISCOVERY_TIMEOUT) -> list[dict[str, Any]]:
    def probe(port: int) -> dict[str, Any] | None:
        try:
            return _health(port, timeout)
        except BlenderBridgeError:
            return None

    ports = blender_ports()
    with ThreadPoolExecutor(max_workers=min(32, len(ports))) as executor:
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
        return _port(port)
    sessions = discover_blender_sessions()
    if not sessions:
        raise BlenderBridgeError(
            "No CGI Blender foreground bridge found in the configured ports",
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
        "import bpy",
        f"source_root = {str(SOURCE_ROOT)!r}",
        "if source_root not in sys.path:",
        "    sys.path.insert(0, source_root)",
        "from cgi_pipeline.execution import execute_api",
        f"api_context = {context!r}",
        "api_context['bpy_module'] = bpy",
        "result = execute_api(",
        f"    {payload.get('api_id', 'blender_exec_code')!r},",
        f"    params={payload.get('api_params') or {}!r},",
        "    context=api_context,",
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
        traceback_text = str(response.get("message") or "CGI Blender execution failed")
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
        _write_audit(task_id, "STARTED", api_id, f"CGI Blender execution started on port {port}")
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
            "recovery_hint": "Start CGI's native_bridge in Blender and pass foreground_port.",
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
