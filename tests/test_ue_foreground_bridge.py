"""Contract tests for the single UE_MCP_Bridge JSON-RPC transport."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cgi_pipeline.hosts.ue import foreground_client


class FakeConnection:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self, timeout=None):
        if self.error is not None:
            raise self.error
        response = self.responses.pop(0)
        if callable(response):
            response = response(self.sent[-1])
        return json.dumps(response)


def _rpc_result(result):
    return lambda sent: {"jsonrpc": "2.0", "id": sent["id"], "result": result}


def _process(project_path, pid=4321):
    return {
        "pid": pid,
        "process": "UnrealEditor.exe",
        "exe": "C:/Program Files/Epic/UE_5.7/UnrealEditor.exe",
        "project_path": str(project_path),
    }


def test_dynamic_bridge_port_reads_matching_running_project_lockfile():
    with tempfile.TemporaryDirectory() as temp:
        project = Path(temp) / "Crowd.uproject"
        lockfile = project.parent / "Saved" / "UE_MCP_Bridge" / "port.json"
        lockfile.parent.mkdir(parents=True)
        lockfile.write_text(json.dumps({"port": 59201, "pid": 4321}), encoding="utf-8")
        with patch.object(
            foreground_client, "_running_unreal_processes", return_value=[_process(project)]
        ):
            ports = foreground_client._dynamic_bridge_ports()

    assert ports[59201]["pid"] == 4321
    assert ports[59201]["engine_family"] == "5.7"


def test_dynamic_bridge_port_rejects_pid_mismatch():
    with tempfile.TemporaryDirectory() as temp:
        project = Path(temp) / "Crowd.uproject"
        lockfile = project.parent / "Saved" / "UE_MCP_Bridge" / "port.json"
        lockfile.parent.mkdir(parents=True)
        lockfile.write_text(json.dumps({"port": 59201, "pid": 9999}), encoding="utf-8")
        with patch.object(
            foreground_client, "_running_unreal_processes", return_value=[_process(project)]
        ):
            assert foreground_client._dynamic_bridge_ports() == {}


def test_probe_bridge_uses_json_rpc_get_build_status():
    connection = FakeConnection([_rpc_result({"success": True, "status": "idle"})])
    with patch.object(foreground_client, "_open_connection", return_value=connection):
        result = foreground_client.probe_bridge(59201, timeout=1)

    assert result["status"] == "idle"
    assert connection.sent[0]["jsonrpc"] == "2.0"
    assert connection.sent[0]["method"] == "get_build_status"


def test_discovery_only_probes_active_project_lockfile_ports():
    candidates = {59201: _process("C:/Crowd.uproject")}
    with patch.object(foreground_client, "_dynamic_bridge_ports", return_value=candidates), patch.object(
        foreground_client, "probe_bridge", return_value={"success": True, "status": "idle"}
    ) as probe:
        sessions = foreground_client.discover_ue_sessions()

    probe.assert_called_once_with(59201, timeout=foreground_client.DEFAULT_DISCOVERY_TIMEOUT)
    assert sessions == [
        {
            "session_id": "pid-4321",
            "server_name": "UE_MCP_Bridge",
            "server_version": "",
            "protocol_version": "2.0",
            "bridge_protocol": "jsonrpc",
            "port": 59201,
            "pid": 4321,
            "process": "UnrealEditor.exe",
            "project_path": "C:/Crowd.uproject",
            "build_status": {"success": True, "status": "idle"},
        }
    ]


def test_get_bridge_session_rejects_unregistered_port():
    with patch.object(foreground_client, "_dynamic_bridge_ports", return_value={}):
        with pytest.raises(foreground_client.UEBridgeError) as raised:
            foreground_client.get_bridge_session(8091)
    assert raised.value.error_type == "SessionNotDiscovered"


def test_execute_action_calls_raw_json_rpc_method():
    session = {"port": 59201, "bridge_protocol": "jsonrpc"}
    connection = FakeConnection([_rpc_result({"success": True, "actorCount": 3})])
    with patch.object(foreground_client, "get_bridge_session", return_value=session), patch.object(
        foreground_client, "_open_connection", return_value=connection
    ):
        result = foreground_client.execute_action(
            59201, "list_level_actors", {"class": "StaticMeshActor"}, timeout=1
        )

    assert result["status"] == "SUCCESS"
    assert result["bridge_protocol"] == "jsonrpc"
    assert connection.sent[0]["method"] == "list_level_actors"
    assert connection.sent[0]["params"] == {"class": "StaticMeshActor"}


def test_execute_action_returns_json_rpc_error_code():
    session = {"port": 59201, "bridge_protocol": "jsonrpc"}
    response = lambda sent: {
        "jsonrpc": "2.0",
        "id": sent["id"],
        "error": {"code": -32601, "message": "method missing"},
    }
    with patch.object(foreground_client, "get_bridge_session", return_value=session), patch.object(
        foreground_client, "_open_connection", return_value=FakeConnection([response])
    ):
        result = foreground_client.execute_action(59201, "missing_method", {}, timeout=1)

    assert result["status"] == "ERROR"
    assert result["error_type"] == "JsonRpcError"
    assert result["error_code"] == -32601


def test_execute_python_returns_result_and_streams():
    session = {"port": 59201, "bridge_protocol": "jsonrpc"}
    payload = {
        "success": True,
        "log_output": [
            {"type": "info", "output": "before"},
            {"type": "info", "output": '__CGI_PIPELINE_RESULT__={"value": 42}'},
        ],
    }
    with patch.object(foreground_client, "get_bridge_session", return_value=session), patch.object(
        foreground_client, "_open_connection", return_value=FakeConnection([_rpc_result(payload)])
    ):
        result = foreground_client.execute_python(59201, "result = {'value': 42}", timeout=1)

    assert result["status"] == "SUCCESS"
    assert result["result"] == {"value": 42}
    assert result["stdout"] == "before"
    assert result["stderr"] == ""


def test_connect_pins_validates_required_fields_before_discovery():
    with patch.object(foreground_client, "get_bridge_session") as discover:
        result = foreground_client.execute_action(
            59201,
            "manage_blueprint",
            {"action": "connect_pins", "blueprintPath": "/Game/BP_Test"},
            timeout=1,
        )
    assert result["status"] == "ERROR"
    assert result["error_type"] == "InvalidArguments"
    discover.assert_not_called()


def test_timeout_and_disconnect_are_structured():
    session = {"port": 59201, "bridge_protocol": "jsonrpc"}
    with patch.object(foreground_client, "get_bridge_session", return_value=session), patch.object(
        foreground_client, "_open_connection", return_value=FakeConnection(error=TimeoutError("slow"))
    ):
        timeout_result = foreground_client.execute_python(59201, "result = 1", timeout=0.01)
    with patch.object(foreground_client, "get_bridge_session", return_value=session), patch.object(
        foreground_client, "_open_connection", side_effect=OSError("refused")
    ):
        disconnect_result = foreground_client.execute_action(59201, "get_build_status", {}, timeout=1)

    assert timeout_result["status"] == "TIMEOUT"
    assert timeout_result["error_type"] == "TimeoutError"
    assert disconnect_result["status"] == "NEEDS_ATTENTION"
    assert disconnect_result["error_type"] == "ConnectionError"
