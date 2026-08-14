"""Foreground JSON-RPC client for UE_MCP_Bridge."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect


HOST = "127.0.0.1"
BRIDGE_PROTOCOL = "jsonrpc"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
RESULT_MARKER = "__CGI_PIPELINE_RESULT__="
DEFAULT_DISCOVERY_TIMEOUT = 0.75
_DISCOVERED_SESSIONS: dict[int, dict] = {}


class UEBridgeError(RuntimeError):
    def __init__(self, message, error_type="UEBridgeError", response=None):
        super().__init__(message)
        self.error_type = error_type
        self.response = response or {}


def _extract_uproject_path(command):
    """Return the .uproject argument from an UnrealEditor command line."""
    for raw_arg in command or ():
        arg = str(raw_arg or "").strip().strip('"')
        if arg.lower().startswith("-project="):
            arg = arg.split("=", 1)[1].strip().strip('"')
        if arg.lower().endswith(".uproject"):
            return os.path.normpath(arg)
    return ""


def _running_unreal_processes():
    """Return running UnrealEditor processes that identify their project."""
    try:
        import psutil

        records = []
        for process in psutil.process_iter(["pid", "name"]):
            try:
                info = process.info
                process_name = info.get("name") or ""
                if "unrealeditor" not in process_name.lower():
                    continue
                project_path = _extract_uproject_path(process.cmdline())
                if not project_path:
                    continue
                records.append(
                    {
                        "pid": int(info.get("pid") or process.pid),
                        "process": process_name or "UnrealEditor",
                        "exe": process.exe() or "",
                        "project_path": project_path,
                    }
                )
            except (psutil.Error, OSError, ValueError, TypeError):
                continue
        return records
    except Exception:
        return []


def _read_bridge_port(project_path, expected_pid=None):
    """Read a running project's validated UE_MCP_Bridge lockfile."""
    if not project_path:
        return None
    lockfile = Path(project_path).parent / "Saved" / "UE_MCP_Bridge" / "port.json"
    try:
        data = json.loads(lockfile.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or isinstance(data.get("port"), bool):
        return None
    try:
        port = int(data["port"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 1 <= port <= 65535:
        return None
    if expected_pid is not None:
        try:
            if data.get("pid") is None or int(data["pid"]) != int(expected_pid):
                return None
        except (TypeError, ValueError):
            return None
    return port


def _dynamic_bridge_ports():
    """Map active project lockfile ports to their UnrealEditor processes."""
    candidates = {}
    for process in _running_unreal_processes():
        port = _read_bridge_port(process.get("project_path"), process.get("pid"))
        if port is None:
            continue
        metadata = dict(process)
        match = re.search(r"UE_(\d+(?:\.\d+)?)", str(process.get("exe", "")), re.IGNORECASE)
        metadata["engine_family"] = match.group(1) if match else ""
        candidates.setdefault(port, metadata)
    return candidates


def _decode_message(raw):
    if isinstance(raw, bytes):
        if len(raw) > MAX_RESPONSE_BYTES:
            raise UEBridgeError("UE bridge response exceeds size limit", "ResponseTooLarge")
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise UEBridgeError("Invalid or oversized UE bridge response", "InvalidResponse")
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UEBridgeError(f"Invalid UE bridge JSON: {exc}", "InvalidResponse") from exc
    if not isinstance(message, dict):
        raise UEBridgeError("UE bridge response must be an object", "InvalidResponse")
    return message


def _recv(connection, timeout):
    try:
        return _decode_message(connection.recv(timeout=timeout))
    except (TimeoutError, UEBridgeError):
        raise
    except Exception as exc:
        raise UEBridgeError(str(exc), "ConnectionError") from exc


def _open_connection(port, timeout):
    return connect(
        f"ws://{HOST}:{int(port)}",
        open_timeout=timeout,
        close_timeout=min(timeout, 1.0),
        max_size=MAX_RESPONSE_BYTES,
    )


def _request(connection, method, params, timeout):
    request_id = uuid.uuid4().hex
    connection.send(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            ensure_ascii=False,
        )
    )
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"UE method {method} exceeded {timeout:.3g}s")
        response = _recv(connection, remaining)
        if response.get("id") != request_id:
            continue
        if response.get("jsonrpc") != "2.0":
            raise UEBridgeError(
                "UE bridge response is not JSON-RPC 2.0", "InvalidResponse", response
            )
        if "error" in response:
            error = response.get("error")
            message = str(error.get("message") or error) if isinstance(error, dict) else str(error)
            raise UEBridgeError(message or "UE bridge returned an error", "JsonRpcError", response)
        if "result" not in response:
            raise UEBridgeError("UE bridge response is missing result", "InvalidResponse", response)
        return response["result"]


def probe_bridge(port, timeout=DEFAULT_DISCOVERY_TIMEOUT):
    """Verify that a registered project port serves UE_MCP_Bridge JSON-RPC."""
    try:
        with _open_connection(port, timeout) as connection:
            build_status = _request(connection, "get_build_status", {}, timeout)
    except TimeoutError as exc:
        raise UEBridgeError(str(exc), "TimeoutError") from exc
    except UEBridgeError:
        raise
    except (OSError, ConnectionError, WebSocketException) as exc:
        raise UEBridgeError(str(exc), "ConnectionError") from exc
    if not isinstance(build_status, dict) or build_status.get("success") is not True:
        detail = build_status.get("error") if isinstance(build_status, dict) else "invalid result"
        raise UEBridgeError(f"get_build_status failed: {detail}", "ProbeError")
    if not isinstance(build_status.get("status"), str):
        raise UEBridgeError("get_build_status result is missing status", "ProbeError", build_status)
    return build_status


def _session_metadata(port, process_info, build_status=None):
    session = {
        "session_id": f"pid-{process_info['pid']}" if process_info.get("pid") else f"port-{port}",
        "server_name": "UE_MCP_Bridge",
        "server_version": "",
        "protocol_version": "2.0",
        "bridge_protocol": BRIDGE_PROTOCOL,
        "port": int(port),
    }
    for field in ("pid", "process", "project_path", "engine_family"):
        if field in process_info:
            session[field] = process_info[field]
    if build_status is not None:
        session["build_status"] = build_status
    return session


def discover_ue_sessions(timeout=DEFAULT_DISCOVERY_TIMEOUT):
    """Discover only bridges registered by currently running Unreal projects."""
    _DISCOVERED_SESSIONS.clear()
    candidates = _dynamic_bridge_ports()
    if not candidates:
        return []

    def probe(item):
        port, metadata = item
        try:
            status = probe_bridge(port, timeout=timeout)
        except (UEBridgeError, WebSocketException, OSError, ConnectionError):
            return None
        return _session_metadata(port, metadata, status)

    workers = min(len(candidates), 16)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        sessions = [session for session in executor.map(probe, candidates.items()) if session]
    for session in sessions:
        _DISCOVERED_SESSIONS[session["port"]] = dict(session)
    return sorted(sessions, key=lambda item: item["port"])


def get_bridge_session(port):
    """Return metadata only for a port owned by a running Unreal project."""
    port = int(port)
    if port in _DISCOVERED_SESSIONS:
        return dict(_DISCOVERED_SESSIONS[port])
    process_info = _dynamic_bridge_ports().get(port)
    if not process_info:
        raise UEBridgeError(
            f"Port {port} is not registered by a running UE_MCP_Bridge project",
            "SessionNotDiscovered",
        )
    return _session_metadata(port, process_info)


def _json_rpc_error_code(error):
    response_error = error.response.get("error") if isinstance(error.response, dict) else None
    return response_error.get("code") if isinstance(response_error, dict) else None


def _python_streams(payload):
    if not isinstance(payload, dict):
        raise UEBridgeError("UE execute_python result must be an object", "InvalidResponse")
    stdout_parts = []
    stderr_parts = []
    log_output = payload.get("log_output")
    if isinstance(log_output, list):
        for entry in log_output:
            if not isinstance(entry, dict):
                continue
            output = str(entry.get("output", "") or "")
            if not output:
                continue
            if str(entry.get("type", "")).lower() in {"error", "warning"}:
                stderr_parts.append(output)
            else:
                stdout_parts.append(output)
    else:
        stdout_parts.append(str(payload.get("output", "") or ""))
    parsed_result, stdout = _split_output("\n".join(stdout_parts))
    stderr = "\n".join(stderr_parts).strip() or str(payload.get("error", "") or "").strip()
    if not stderr and payload.get("success") is False:
        stderr = str(payload.get("result", "") or "").strip()
    return parsed_result, stdout, stderr


def _wrap_code(code, description):
    filename = f"<ue.editor.python.execute: {description or 'foreground'}>"
    return "\n".join(
        [
            "import json as _cgi_json",
            f"_cgi_code = {code!r}",
            "_cgi_namespace = {'__name__': '__cgi_pipeline_ue_foreground__'}",
            f"exec(compile(_cgi_code, {filename!r}, 'exec'), _cgi_namespace, _cgi_namespace)",
            f"print({RESULT_MARKER!r} + _cgi_json.dumps(_cgi_namespace.get('result'), ensure_ascii=False, default=repr))",
        ]
    )


def _split_output(output):
    lines = str(output or "").splitlines()
    result = None
    kept = []
    for line in lines:
        if line.startswith(RESULT_MARKER):
            try:
                result = json.loads(line[len(RESULT_MARKER):])
            except json.JSONDecodeError:
                result = line[len(RESULT_MARKER):]
        else:
            kept.append(line)
    return result, "\n".join(kept).strip()


def _validate_action_payload(action, payload):
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise UEBridgeError("UE action payload must be an object", "InvalidArguments")
    outgoing = dict(payload)
    if action != "manage_blueprint":
        return outgoing
    sub_action = outgoing.get("subAction") or outgoing.get("action")
    if sub_action not in {"connect_pins", "get_nodes"}:
        return outgoing
    blueprint_path = outgoing.get("blueprintPath") or outgoing.get("assetPath")
    if not isinstance(blueprint_path, str) or not blueprint_path.strip():
        raise UEBridgeError(
            f"{sub_action} requires non-empty blueprintPath or assetPath", "InvalidArguments"
        )
    if sub_action == "connect_pins":
        required = ("graphName", "fromNodeId", "fromPinName", "toNodeId", "toPinName")
        missing = [
            field
            for field in required
            if not isinstance(outgoing.get(field), str) or not outgoing[field].strip()
        ]
        if missing:
            raise UEBridgeError(
                f"connect_pins requires non-empty fields: {', '.join(missing)}",
                "InvalidArguments",
            )
    elif "graphName" in outgoing and (
        not isinstance(outgoing["graphName"], str) or not outgoing["graphName"].strip()
    ):
        raise UEBridgeError("get_nodes graphName must be a non-empty string", "InvalidArguments")
    return outgoing


def _error_result(port, session, started, exc, *, action="", connected=False):
    if isinstance(exc, TimeoutError):
        status = "TIMEOUT" if connected else "NEEDS_ATTENTION"
        error_type = "TimeoutError" if connected else "ConnectionTimeout"
        error_code = None
    elif isinstance(exc, UEBridgeError):
        status = "ERROR" if exc.error_type in {"InvalidArguments", "JsonRpcError"} else "NEEDS_ATTENTION"
        error_type = exc.error_type
        error_code = _json_rpc_error_code(exc)
    else:
        status = "NEEDS_ATTENTION"
        error_type = "ConnectionError"
        error_code = None
    result = {
        "status": status,
        "bridge_protocol": BRIDGE_PROTOCOL,
        "session": session,
        "result": None,
        "error_type": error_type,
        "error": str(exc),
        "duration_seconds": round(time.monotonic() - started, 6),
    }
    if error_code is not None:
        result["error_code"] = error_code
    if action:
        result["action"] = action
    return result


def execute_python(port, code, description="", timeout=120.0):
    """Execute Python through one registered UE_MCP_Bridge session."""
    started = time.monotonic()
    session = {"port": int(port), "bridge_protocol": BRIDGE_PROTOCOL}
    connected = False
    try:
        session = get_bridge_session(port)
        with _open_connection(port, timeout) as connection:
            connected = True
            payload = _request(
                connection, "execute_python", {"code": _wrap_code(code, description)}, timeout
            )
        parsed_result, stdout, stderr = _python_streams(payload)
        success = not (isinstance(payload, dict) and payload.get("success") is False)
        return {
            "status": "SUCCESS" if success else "ERROR",
            "bridge_protocol": BRIDGE_PROTOCOL,
            "session": session,
            "result": parsed_result,
            "stdout": stdout,
            "stderr": stderr,
            "traceback": stderr if "Traceback" in stderr else "",
            "error_type": "PythonError" if not success else "",
            "error": str(payload.get("error", "") or stderr) if not success else "",
            "progress": [],
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except (TimeoutError, UEBridgeError, OSError, ConnectionError, WebSocketException) as exc:
        result = _error_result(port, session, started, exc, connected=connected)
        result.update({"stdout": "", "stderr": "", "traceback": ""})
        return result


def execute_action(port, action, payload=None, timeout=120.0):
    """Invoke one UE_MCP_Bridge JSON-RPC method."""
    started = time.monotonic()
    session = {"port": int(port), "bridge_protocol": BRIDGE_PROTOCOL}
    connected = False
    try:
        outgoing_payload = _validate_action_payload(action, payload)
        session = get_bridge_session(port)
        with _open_connection(port, timeout) as connection:
            connected = True
            result = _request(connection, action, outgoing_payload, timeout)
        success = not (isinstance(result, dict) and result.get("success") is False)
        return {
            "status": "SUCCESS" if success else "ERROR",
            "bridge_protocol": BRIDGE_PROTOCOL,
            "session": session,
            "action": action,
            "result": result,
            "error_type": "HandlerError" if not success else "",
            "error": str(result.get("error", "")) if not success else "",
            "progress": [],
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except (TimeoutError, UEBridgeError, OSError, ConnectionError, WebSocketException) as exc:
        return _error_result(port, session, started, exc, action=action, connected=connected)
