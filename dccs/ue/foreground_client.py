"""Foreground client for the UE MCP Automation Bridge.

The Unreal plugin is an internal localhost transport. AI clients only use the
UE tools exposed by ``cgi_pipeline_mcp``.
"""

import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from websockets.sync.client import connect
from websockets.exceptions import WebSocketException


HOST = "127.0.0.1"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
RESULT_MARKER = "__CGI_PIPELINE_RESULT__="
DEFAULT_DISCOVERY_TIMEOUT = 0.75
LEGACY_BRIDGE_PROTOCOL = "legacy"
NATIVE_BRIDGE_PROTOCOL = "native"
_DISCOVERED_BRIDGES = {}


class UEBridgeError(RuntimeError):
    def __init__(self, message, error_type="UEBridgeError", response=None):
        super().__init__(message)
        self.error_type = error_type
        self.response = response or {}


def ue_port_range():
    start = int(os.getenv("UE_FOREGROUND_PORT_START", "8090"))
    end = int(os.getenv("UE_FOREGROUND_PORT_END", "8099"))
    if end < start:
        start, end = end, start
    return range(start, end + 1)


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
    """Return running UnrealEditor processes that carry a project path.

    This is deliberately best-effort.  The WebSocket handshake remains the
    authority; process inspection only supplies candidate ports and metadata.
    """
    try:
        import psutil

        records = []
        for process in psutil.process_iter(["pid", "name"]):
            try:
                info = process.info
                process_name = info.get("name") or ""
                if "unrealeditor" not in process_name.lower():
                    continue
                command = process.cmdline()
                project_path = _extract_uproject_path(command)
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
    """Read and validate a project's UE_MCP_Bridge port lockfile."""
    if not project_path:
        return None
    lockfile = Path(project_path).parent / "Saved" / "UE_MCP_Bridge" / "port.json"
    try:
        data = json.loads(lockfile.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw_port = data.get("port")
    if isinstance(raw_port, bool):
        return None
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        return None
    if not 1 <= port <= 65535:
        return None
    raw_pid = data.get("pid")
    if expected_pid is not None:
        try:
            if raw_pid is None or int(raw_pid) != int(expected_pid):
                return None
        except (TypeError, ValueError):
            return None
    return port


def _dynamic_bridge_ports():
    """Map live project bridge ports to process metadata.

    UE_MCP_Bridge selects a free port per editor and publishes it under the
    project.  Reading only files belonging to running UnrealEditor processes
    avoids stale lockfiles becoming discovery candidates.  The later
    WebSocket handshake still filters stale or non-bridge listeners.
    """
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


def _hello_payload():
    payload = {"type": "bridge_hello"}
    token = os.getenv("UE_BRIDGE_CAPABILITY_TOKEN", "").strip()
    if token:
        payload["capabilityToken"] = token
    return payload


def _recv(connection, timeout):
    try:
        return _decode_message(connection.recv(timeout=timeout))
    except TimeoutError:
        raise
    except UEBridgeError:
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


def _handshake_on_connection(connection, timeout):
    connection.send(json.dumps(_hello_payload(), ensure_ascii=False))
    response = _recv(connection, timeout)
    if response.get("type") == "bridge_error":
        raise UEBridgeError(
            response.get("error", "UE bridge rejected the handshake"),
            "AuthenticationError",
            response,
        )
    if response.get("type") != "bridge_ack":
        raise UEBridgeError("UE bridge did not return bridge_ack", "HandshakeError", response)
    return response


def _native_request_on_connection(connection, method, params, timeout):
    request_id = uuid.uuid4().hex
    connection.send(
        json.dumps(
            {"id": request_id, "method": method, "params": params},
            ensure_ascii=False,
        )
    )
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"UE native method {method} exceeded {timeout:.3g}s")
        response = _recv(connection, remaining)
        if response.get("id") != request_id:
            continue
        if response.get("jsonrpc") != "2.0":
            raise UEBridgeError(
                "UE native bridge response is not JSON-RPC 2.0",
                "InvalidResponse",
                response,
            )
        if "error" in response:
            error = response.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or error)
            else:
                message = str(error or "UE native bridge returned an error")
            raise UEBridgeError(message, "JsonRpcError", response)
        if "result" not in response:
            raise UEBridgeError(
                "UE native bridge response is missing result",
                "InvalidResponse",
                response,
            )
        return response["result"]


def native_handshake(port, timeout=DEFAULT_DISCOVERY_TIMEOUT):
    try:
        with _open_connection(port, timeout) as connection:
            build_status = _native_request_on_connection(
                connection,
                "get_build_status",
                {},
                timeout,
            )
    except TimeoutError as exc:
        raise UEBridgeError(str(exc), "TimeoutError") from exc
    except UEBridgeError:
        raise
    except (OSError, ConnectionError, WebSocketException) as exc:
        raise UEBridgeError(str(exc), "ConnectionError") from exc
    if not isinstance(build_status, dict) or build_status.get("success") is not True:
        raise UEBridgeError(
            str(
                build_status.get("error") or "get_build_status failed"
                if isinstance(build_status, dict)
                else "get_build_status returned an invalid result"
            ),
            "NativeProbeError",
            build_status if isinstance(build_status, dict) else {},
        )
    if not isinstance(build_status.get("status"), str):
        raise UEBridgeError(
            "get_build_status result is missing status",
            "NativeProbeError",
            build_status,
        )
    return {
        "port": int(port),
        "bridge_protocol": NATIVE_BRIDGE_PROTOCOL,
        "serverName": "UE_MCP_Bridge",
        "serverVersion": "",
        "protocolVersion": "2.0",
        "buildStatus": build_status,
    }


def handshake(port, timeout=DEFAULT_DISCOVERY_TIMEOUT):
    try:
        with _open_connection(port, timeout) as connection:
            response = _handshake_on_connection(connection, timeout)
    except TimeoutError as exc:
        raise UEBridgeError(str(exc), "TimeoutError") from exc
    except UEBridgeError:
        raise
    except (OSError, ConnectionError, WebSocketException) as exc:
        raise UEBridgeError(str(exc), "ConnectionError") from exc
    response = dict(response)
    response["port"] = int(port)
    response["bridge_protocol"] = LEGACY_BRIDGE_PROTOCOL
    return response


def _listener_process_info(ports):
    """Best-effort PID/project discovery; handshake remains the source of truth."""
    try:
        import psutil

        listeners = {}
        wanted = set(ports)
        for connection in psutil.net_connections(kind="tcp"):
            try:
                if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                    continue
                port = int(connection.laddr.port)
                if port not in wanted or not connection.pid:
                    continue
                process = psutil.Process(connection.pid)
                command = process.cmdline()
                project = next((arg for arg in command if arg.lower().endswith(".uproject")), "")
                engine = ""
                match = re.search(r"UE_(\d+(?:\.\d+)?)", process.exe(), re.IGNORECASE)
                if match:
                    engine = match.group(1)
                listeners[port] = {
                    "pid": process.pid,
                    "process": process.name(),
                    "project_path": project,
                    "engine_family": engine,
                }
            except (psutil.Error, OSError, ValueError, TypeError):
                continue
        return listeners
    except Exception:
        return {}


def discover_ue_sessions(timeout=DEFAULT_DISCOVERY_TIMEOUT):
    _DISCOVERED_BRIDGES.clear()
    static_ports = list(ue_port_range())
    dynamic_info = _dynamic_bridge_ports()
    ports = sorted(set(static_ports).union(dynamic_info))

    def probe(port):
        try:
            if port in dynamic_info:
                return native_handshake(port, timeout=timeout)
            return handshake(port, timeout=timeout)
        except (UEBridgeError, WebSocketException, OSError, ConnectionError):
            return None

    with ThreadPoolExecutor(max_workers=len(ports)) as executor:
        acknowledgements = [ack for ack in executor.map(probe, ports) if ack]

    acknowledged_ports = [ack["port"] for ack in acknowledgements]
    process_info = _listener_process_info(acknowledged_ports)
    for port in acknowledged_ports:
        if port in dynamic_info:
            process_info.setdefault(port, dynamic_info[port])
    grouped = {}
    for ack in acknowledgements:
        port = int(ack["port"])
        metadata = process_info.get(port, {})
        protocol = ack.get("bridge_protocol", LEGACY_BRIDGE_PROTOCOL)
        _DISCOVERED_BRIDGES[port] = (protocol, dict(metadata))
        session_id = ack.get("sessionId") or (
            f"pid-{metadata['pid']}" if metadata.get("pid") else f"port-{port}"
        )
        process_key = metadata.get("pid") or f"port-{port}"
        group_key = (protocol, process_key)
        session = grouped.setdefault(
            group_key,
            {
                "session_id": session_id,
                "server_name": ack.get("serverName", "UnrealEditor"),
                "server_version": ack.get("serverVersion", ""),
                "protocol_version": ack.get("protocolVersion"),
                "bridge_protocol": protocol,
                "ports": [],
            },
        )
        session["ports"].append(port)
        if protocol == NATIVE_BRIDGE_PROTOCOL:
            session["build_status"] = ack.get("buildStatus")
        if metadata:
            session.update(metadata)

    sessions = []
    for session in grouped.values():
        session["ports"].sort()
        preferred = int(os.getenv("UE_FOREGROUND_PORT", "8091"))
        session["port"] = preferred if preferred in session["ports"] else session["ports"][0]
        sessions.append(session)
    native_pids = {
        session.get("pid")
        for session in sessions
        if session.get("bridge_protocol") == NATIVE_BRIDGE_PROTOCOL and session.get("pid")
    }
    sessions = [
        session
        for session in sessions
        if not (
            session.get("bridge_protocol") == LEGACY_BRIDGE_PROTOCOL
            and session.get("pid") in native_pids
        )
    ]
    return sorted(sessions, key=lambda item: item["port"])


def _bridge_for_port(port):
    port = int(port)
    if port in _DISCOVERED_BRIDGES:
        return _DISCOVERED_BRIDGES[port]
    if port in ue_port_range():
        return LEGACY_BRIDGE_PROTOCOL, {}
    dynamic = _dynamic_bridge_ports().get(port)
    if dynamic:
        return NATIVE_BRIDGE_PROTOCOL, dynamic
    return LEGACY_BRIDGE_PROTOCOL, {}


def _session_metadata(port, bridge_protocol, process_info=None, ack=None):
    session = {"port": int(port), "bridge_protocol": bridge_protocol}
    if ack:
        session.update(
            {
                "session_id": ack.get("sessionId", ""),
                "server_name": ack.get("serverName", "UnrealEditor"),
                "server_version": ack.get("serverVersion", ""),
            }
        )
    elif bridge_protocol == NATIVE_BRIDGE_PROTOCOL:
        session.update(
            {
                "session_id": "",
                "server_name": "UE_MCP_Bridge",
                "server_version": "",
            }
        )
    if process_info:
        for field in ("pid", "process", "project_path", "engine_family"):
            if field in process_info:
                session[field] = process_info[field]
        if not session.get("session_id") and process_info.get("pid"):
            session["session_id"] = f"pid-{process_info['pid']}"
    return session


def get_bridge_session(port):
    """Return cached or lockfile-derived metadata for one selected UE port."""
    bridge_protocol, process_info = _bridge_for_port(port)
    return _session_metadata(port, bridge_protocol, process_info)


def _json_rpc_error_code(error):
    response_error = error.response.get("error") if isinstance(error.response, dict) else None
    return response_error.get("code") if isinstance(response_error, dict) else None


def _native_python_streams(payload):
    if not isinstance(payload, dict):
        raise UEBridgeError(
            "UE native execute_python result must be an object",
            "InvalidResponse",
        )

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
    stderr = "\n".join(stderr_parts).strip()
    if not stderr:
        stderr = str(payload.get("error", "") or "").strip()
    if not stderr and payload.get("success") is False:
        stderr = str(payload.get("result", "") or "").strip()
    return parsed_result, stdout, stderr


def _wrap_code(code, description):
    filename = f"<ue_exec_code: {description or 'foreground'}>"
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


def _fallback_response(message):
    if message.get("type") != "automation_event" or message.get("event") != "response_fallback":
        return None
    fallback = message.get("result") or {}
    return {
        "type": "automation_response",
        "requestId": message.get("requestId"),
        "success": bool(fallback.get("success")),
        "message": fallback.get("message", ""),
        "error": fallback.get("error", ""),
        "result": fallback.get("payload") or {},
    }


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
            f"{sub_action} requires non-empty blueprintPath or assetPath",
            "InvalidArguments",
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


def execute_python(port, code, description="", timeout=120.0):
    started = time.monotonic()
    ack = None
    connected = False
    bridge_protocol, process_info = _bridge_for_port(port)
    try:
        with _open_connection(port, timeout) as connection:
            connected = True
            if bridge_protocol == NATIVE_BRIDGE_PROTOCOL:
                payload = _native_request_on_connection(
                    connection,
                    "execute_python",
                    {"code": _wrap_code(code, description)},
                    timeout,
                )
                progress = []
            else:
                request_id = uuid.uuid4().hex
                ack = _handshake_on_connection(connection, min(timeout, 5.0))
                connection.send(
                    json.dumps(
                        {
                            "type": "automation_request",
                            "requestId": request_id,
                            "action": "system_control",
                            "payload": {
                                "action": "execute_python",
                                "code": _wrap_code(code, description),
                            },
                        },
                        ensure_ascii=False,
                    )
                )

                deadline = time.monotonic() + timeout
                progress = []
                response = None
                while response is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"UE execute_python exceeded {timeout:.3g}s")
                    message = _recv(connection, remaining)
                    fallback = _fallback_response(message)
                    if fallback is not None:
                        message = fallback
                    if message.get("type") == "progress_update":
                        progress.append(message)
                        continue
                    if message.get("type") != "automation_response":
                        continue
                    if message.get("requestId") != request_id:
                        continue
                    response = message
                payload = response.get("result") or {}

        if bridge_protocol == NATIVE_BRIDGE_PROTOCOL:
            parsed_result, stdout, stderr = _native_python_streams(payload)
            success = not (isinstance(payload, dict) and payload.get("success") is False)
            error_type = "PythonError" if not success else ""
            error_message = str(payload.get("error", "") or stderr) if not success else ""
        else:
            parsed_result, stdout = _split_output(payload.get("output", ""))
            stderr = str(payload.get("error", "") or "").strip()
            success = bool(response.get("success"))
            error_type = response.get("error", "") if not success else ""
            error_message = response.get("message", "") if not success else ""
        return {
            "status": "SUCCESS" if success else "ERROR",
            "bridge_protocol": bridge_protocol,
            "session": _session_metadata(port, bridge_protocol, process_info, ack),
            "result": parsed_result,
            "stdout": stdout,
            "stderr": stderr,
            "traceback": stderr if "Traceback" in stderr else "",
            "error_type": error_type,
            "error": error_message,
            "progress": progress,
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except TimeoutError as exc:
        return {
            "status": "TIMEOUT" if connected else "NEEDS_ATTENTION",
            "bridge_protocol": bridge_protocol,
            "session": _session_metadata(port, bridge_protocol, process_info, ack),
            "result": None,
            "stdout": "",
            "stderr": "",
            "traceback": "",
            "error_type": "TimeoutError" if connected else "ConnectionTimeout",
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except UEBridgeError as exc:
        if exc.error_type == "TimeoutError":
            status = "TIMEOUT"
        elif exc.error_type == "JsonRpcError":
            status = "ERROR"
        else:
            status = "NEEDS_ATTENTION"
        return {
            "status": status,
            "bridge_protocol": bridge_protocol,
            "session": _session_metadata(port, bridge_protocol, process_info, ack),
            "result": None,
            "stdout": "",
            "stderr": "",
            "traceback": "",
            "error_type": exc.error_type,
            "error_code": _json_rpc_error_code(exc),
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except (OSError, ConnectionError, WebSocketException) as exc:
        return {
            "status": "NEEDS_ATTENTION",
            "bridge_protocol": bridge_protocol,
            "session": _session_metadata(port, bridge_protocol, process_info, ack),
            "result": None,
            "stdout": "",
            "stderr": "",
            "traceback": "",
            "error_type": "ConnectionError",
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 6),
        }


def execute_action(port, action, payload=None, timeout=120.0):
    """Execute one structured plugin action through the foreground bridge."""
    started = time.monotonic()
    ack = None
    connected = False
    bridge_protocol, process_info = _bridge_for_port(port)
    try:
        if bridge_protocol == NATIVE_BRIDGE_PROTOCOL:
            if payload is None:
                payload = {}
            if not isinstance(payload, dict):
                raise UEBridgeError("UE action payload must be an object", "InvalidArguments")
            outgoing_payload = dict(payload)
        else:
            outgoing_payload = _validate_action_payload(action, payload)
        with _open_connection(port, timeout) as connection:
            connected = True
            if bridge_protocol == NATIVE_BRIDGE_PROTOCOL:
                result = _native_request_on_connection(
                    connection,
                    action,
                    outgoing_payload,
                    timeout,
                )
                progress = []
            else:
                request_id = uuid.uuid4().hex
                ack = _handshake_on_connection(connection, min(timeout, 5.0))
                connection.send(
                    json.dumps(
                        {
                            "type": "automation_request",
                            "requestId": request_id,
                            "action": action,
                            "payload": outgoing_payload,
                        },
                        ensure_ascii=False,
                    )
                )

                deadline = time.monotonic() + timeout
                progress = []
                response = None
                while response is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"UE action {action} exceeded {timeout:.3g}s")
                    message = _recv(connection, remaining)
                    fallback = _fallback_response(message)
                    if fallback is not None:
                        message = fallback
                    if message.get("type") == "progress_update":
                        progress.append(message)
                        continue
                    if message.get("type") != "automation_response":
                        continue
                    if message.get("requestId") != request_id:
                        continue
                    response = message
                result = response.get("result")

        if bridge_protocol == NATIVE_BRIDGE_PROTOCOL:
            success = not (isinstance(result, dict) and result.get("success") is False)
            error_type = "NativeHandlerError" if not success else ""
            error_message = str(result.get("error", "")) if not success else ""
        else:
            success = bool(response.get("success"))
            error_type = response.get("error", "") if not success else ""
            error_message = response.get("message", "") if not success else ""
        return {
            "status": "SUCCESS" if success else "ERROR",
            "bridge_protocol": bridge_protocol,
            "session": _session_metadata(port, bridge_protocol, process_info, ack),
            "action": action,
            "result": result,
            "error_type": error_type,
            "error": error_message,
            "progress": progress,
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except TimeoutError as exc:
        return {
            "status": "TIMEOUT" if connected else "NEEDS_ATTENTION",
            "bridge_protocol": bridge_protocol,
            "session": _session_metadata(port, bridge_protocol, process_info, ack),
            "action": action,
            "result": None,
            "error_type": "TimeoutError" if connected else "ConnectionTimeout",
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except UEBridgeError as exc:
        if exc.error_type == "TimeoutError":
            status = "TIMEOUT"
        elif exc.error_type in {"InvalidArguments", "JsonRpcError"}:
            status = "ERROR"
        else:
            status = "NEEDS_ATTENTION"
        return {
            "status": status,
            "bridge_protocol": bridge_protocol,
            "session": _session_metadata(port, bridge_protocol, process_info, ack),
            "action": action,
            "result": None,
            "error_type": exc.error_type,
            "error_code": _json_rpc_error_code(exc),
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except (OSError, ConnectionError, WebSocketException) as exc:
        return {
            "status": "NEEDS_ATTENTION",
            "bridge_protocol": bridge_protocol,
            "session": _session_metadata(port, bridge_protocol, process_info, ack),
            "action": action,
            "result": None,
            "error_type": "ConnectionError",
            "error": str(exc),
            "duration_seconds": round(time.monotonic() - started, 6),
        }
