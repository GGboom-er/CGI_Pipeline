import json
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dccs.ue import foreground_client


class FakeConnection:
    def __init__(self, messages=None, error=None):
        self.messages = list(messages or [])
        self.error = error
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send(self, message):
        self.sent.append(json.loads(message))

    def recv(self, timeout=None):
        if self.messages:
            return json.dumps(self.messages.pop(0))
        if self.error:
            raise self.error
        raise TimeoutError("no response")


def _ack(session_id="session-a"):
    return {
        "type": "bridge_ack",
        "serverName": "UnrealEditor",
        "serverVersion": "unreal-engine",
        "sessionId": session_id,
        "protocolVersion": 1,
    }


class PlainHttpHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(4096)
        self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")


class UEForegroundBridgeTests(unittest.TestCase):
    def test_dynamic_bridge_port_reads_project_lockfile_for_running_editor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir) / "Crowd.uproject"
            project_path.write_text("{}", encoding="utf-8")
            lockfile = project_path.parent / "Saved" / "UE_MCP_Bridge" / "port.json"
            lockfile.parent.mkdir(parents=True)
            lockfile.write_text(json.dumps({"port": 59201, "pid": 4321}), encoding="utf-8")

            process = {
                "pid": 4321,
                "process": "UnrealEditor.exe",
                "exe": "C:/UE/UnrealEditor.exe",
                "project_path": str(project_path),
            }
            with patch.object(foreground_client, "_running_unreal_processes", return_value=[process]):
                candidates = foreground_client._dynamic_bridge_ports()

        self.assertEqual(candidates[59201]["project_path"], str(project_path))
        self.assertEqual(candidates[59201]["pid"], 4321)

    def test_dynamic_bridge_port_requires_matching_lockfile_pid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir) / "Crowd.uproject"
            project_path.write_text("{}", encoding="utf-8")
            lockfile = project_path.parent / "Saved" / "UE_MCP_Bridge" / "port.json"
            lockfile.parent.mkdir(parents=True)

            for lock_data in ({"port": 59201}, {"port": 59201, "pid": 9999}):
                with self.subTest(lock_data=lock_data):
                    lockfile.write_text(json.dumps(lock_data), encoding="utf-8")
                    process = {
                        "pid": 4321,
                        "process": "UnrealEditor.exe",
                        "exe": "C:/UE/UnrealEditor.exe",
                        "project_path": str(project_path),
                    }
                    with patch.object(
                        foreground_client, "_running_unreal_processes", return_value=[process]
                    ):
                        self.assertEqual(foreground_client._dynamic_bridge_ports(), {})

    def test_native_discovery_uses_get_build_status_json_request(self):
        connection = FakeConnection(
            [
                {
                    "jsonrpc": "2.0",
                    "id": "request-id",
                    "result": {"success": True, "status": "idle"},
                }
            ]
        )
        with patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            acknowledgement = foreground_client.native_handshake(59201, timeout=1)

        self.assertEqual(
            connection.sent,
            [{"id": "request-id", "method": "get_build_status", "params": {}}],
        )
        self.assertEqual(acknowledgement["bridge_protocol"], "native")
        self.assertEqual(acknowledgement["buildStatus"]["status"], "idle")

    def test_native_discovery_rejects_invalid_build_status_result(self):
        connection = FakeConnection(
            [{"jsonrpc": "2.0", "id": "request-id", "result": {"success": True}}]
        )
        with patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            with self.assertRaises(foreground_client.UEBridgeError) as raised:
                foreground_client.native_handshake(59201, timeout=1)

        self.assertEqual(raised.exception.error_type, "NativeProbeError")

    def test_discovery_probes_dynamic_project_port_in_addition_to_legacy_range(self):
        observed_legacy_ports = []
        observed_native_ports = []

        def fake_handshake(port, timeout=foreground_client.DEFAULT_DISCOVERY_TIMEOUT):
            observed_legacy_ports.append(port)
            raise foreground_client.UEBridgeError("closed", "ConnectionError")

        def fake_native_handshake(port, timeout=foreground_client.DEFAULT_DISCOVERY_TIMEOUT):
            observed_native_ports.append(port)
            return {
                "port": port,
                "bridge_protocol": foreground_client.NATIVE_BRIDGE_PROTOCOL,
                "serverName": "UE_MCP_Bridge",
                "serverVersion": "",
                "protocolVersion": "2.0",
                "buildStatus": {"success": True, "status": "idle"},
            }

        dynamic_info = {
            59201: {
                "pid": 4321,
                "process": "UnrealEditor.exe",
                "project_path": "S:/Project/ysj/crowd/YSJ_Crowd.uproject",
                "engine_family": "5.7",
            }
        }
        with patch.object(foreground_client, "ue_port_range", return_value=range(8090, 8091)), patch.object(
            foreground_client, "_dynamic_bridge_ports", return_value=dynamic_info
        ), patch.object(foreground_client, "handshake", side_effect=fake_handshake), patch.object(
            foreground_client, "native_handshake", side_effect=fake_native_handshake
        ), patch.object(
            foreground_client, "_listener_process_info", return_value={}
        ):
            sessions = foreground_client.discover_ue_sessions()

        self.assertEqual(observed_legacy_ports, [8090])
        self.assertEqual(observed_native_ports, [59201])
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["port"], 59201)
        self.assertEqual(sessions[0]["project_path"], dynamic_info[59201]["project_path"])
        self.assertEqual(sessions[0]["bridge_protocol"], "native")

    def test_execute_action_forwards_structured_payload(self):
        connection = FakeConnection([
            _ack(),
            {
                "type": "automation_response",
                "requestId": "request-id",
                "success": True,
                "error": "",
                "result": {"nodeCount": 3},
            },
        ])

        with patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            result = foreground_client.execute_action(
                8091,
                "manage_blueprint",
                {"action": "get_graph_details", "blueprintPath": "/Game/BP_Test"},
                timeout=1,
            )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["bridge_protocol"], "legacy")
        self.assertEqual(result["result"], {"nodeCount": 3})
        self.assertEqual(connection.sent[1]["action"], "manage_blueprint")
        self.assertEqual(connection.sent[1]["payload"]["action"], "get_graph_details")

    def test_native_execute_action_uses_raw_method_and_params(self):
        connection = FakeConnection(
            [
                {
                    "jsonrpc": "2.0",
                    "id": "request-id",
                    "result": {"success": True, "actorCount": 3},
                }
            ]
        )
        dynamic_info = {59201: {"pid": 4321, "project_path": "Crowd.uproject"}}
        with patch.object(
            foreground_client, "_dynamic_bridge_ports", return_value=dynamic_info
        ), patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            result = foreground_client.execute_action(
                59201,
                "get_actors",
                {"className": "StaticMeshActor"},
                timeout=1,
            )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["bridge_protocol"], "native")
        self.assertEqual(result["session"]["pid"], 4321)
        self.assertEqual(result["result"]["actorCount"], 3)
        self.assertEqual(
            connection.sent,
            [
                {
                    "id": "request-id",
                    "method": "get_actors",
                    "params": {"className": "StaticMeshActor"},
                }
            ],
        )

    def test_get_bridge_session_exposes_native_project_metadata(self):
        dynamic_info = {59201: {"pid": 4321, "project_path": "Crowd.uproject"}}
        foreground_client._DISCOVERED_BRIDGES.clear()
        with patch.object(
            foreground_client, "_dynamic_bridge_ports", return_value=dynamic_info
        ):
            session = foreground_client.get_bridge_session(59201)

        self.assertEqual(session["bridge_protocol"], "native")
        self.assertEqual(session["project_path"], "Crowd.uproject")
        self.assertEqual(session["session_id"], "pid-4321")

    def test_native_execute_action_parses_json_rpc_error(self):
        connection = FakeConnection(
            [
                {
                    "jsonrpc": "2.0",
                    "id": "request-id",
                    "error": {"code": -32601, "message": "Unknown method: bad_method"},
                }
            ]
        )
        with patch.object(
            foreground_client,
            "_dynamic_bridge_ports",
            return_value={59201: {"pid": 4321}},
        ), patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            result = foreground_client.execute_action(59201, "bad_method", {}, timeout=1)

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error_type"], "JsonRpcError")
        self.assertEqual(result["error_code"], -32601)
        self.assertIn("Unknown method", result["error"])

    def test_native_execute_action_parses_handler_error_result(self):
        connection = FakeConnection(
            [
                {
                    "jsonrpc": "2.0",
                    "id": "request-id",
                    "result": {"success": False, "error": "Actor not found"},
                }
            ]
        )
        with patch.object(
            foreground_client,
            "_dynamic_bridge_ports",
            return_value={59201: {"pid": 4321}},
        ), patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            result = foreground_client.execute_action(59201, "delete_actor", {}, timeout=1)

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error_type"], "NativeHandlerError")
        self.assertEqual(result["error"], "Actor not found")

    def test_connect_pins_rejects_missing_canonical_fields_before_connecting(self):
        payload = {
            "action": "connect_pins",
            "blueprintPath": "/Game/BP_Test",
            "graphName": "EventGraph",
            "fromNodeId": "node-a",
            "fromPinName": "then",
            "toNodeId": "node-b",
            "toPinName": "execute",
        }

        for field in ("graphName", "fromNodeId", "fromPinName", "toNodeId", "toPinName"):
            with self.subTest(field=field), patch.object(foreground_client, "connect") as connect_mock:
                invalid = dict(payload)
                invalid.pop(field)
                result = foreground_client.execute_action(
                    8091,
                    "manage_blueprint",
                    invalid,
                    timeout=1,
                )

            self.assertEqual(result["status"], "ERROR")
            self.assertEqual(result["error_type"], "InvalidArguments")
            self.assertIn(field, result["error"])
            connect_mock.assert_not_called()

    def test_get_nodes_requires_blueprint_path_before_connecting(self):
        with patch.object(foreground_client, "connect") as connect_mock:
            result = foreground_client.execute_action(
                8091,
                "manage_blueprint",
                {"action": "get_nodes", "graphName": "EventGraph"},
                timeout=1,
            )

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error_type"], "InvalidArguments")
        self.assertIn("blueprintPath", result["error"])
        connect_mock.assert_not_called()

    def test_get_nodes_forwards_valid_blueprint_and_graph(self):
        connection = FakeConnection([
            _ack(),
            {
                "type": "automation_response",
                "requestId": "request-id",
                "success": True,
                "error": "",
                "result": {"nodes": []},
            },
        ])
        with patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            result = foreground_client.execute_action(
                8091,
                "manage_blueprint",
                {
                    "action": "get_nodes",
                    "blueprintPath": "/Game/BP_Test",
                    "graphName": "EventGraph",
                },
                timeout=1,
            )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(connection.sent[1]["payload"]["action"], "get_nodes")
        self.assertEqual(connection.sent[1]["payload"]["graphName"], "EventGraph")

    def test_execute_python_returns_result_and_stdout(self):
        marker = foreground_client.RESULT_MARKER
        connection = FakeConnection([
            _ack(),
            {"type": "progress_update", "requestId": "ignored", "percent": 10},
            {
                "type": "automation_response",
                "requestId": "request-id",
                "success": True,
                "error": "",
                "result": {
                    "output": f"hello\n{marker}{{\"value\": 3}}\n",
                    "error": "",
                },
            },
        ])

        with patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            result = foreground_client.execute_python(
                8091,
                "print('hello')\nresult = {'value': 3}",
                timeout=1,
            )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["result"], {"value": 3})
        self.assertEqual(result["stdout"], "hello")
        self.assertEqual(result["session"]["session_id"], "session-a")
        self.assertEqual(connection.sent[0]["type"], "bridge_hello")
        self.assertEqual(connection.sent[1]["action"], "system_control")
        self.assertEqual(connection.sent[1]["payload"]["action"], "execute_python")

    def test_native_execute_python_returns_result_and_split_streams(self):
        marker = foreground_client.RESULT_MARKER
        connection = FakeConnection(
            [
                {
                    "jsonrpc": "2.0",
                    "id": "request-id",
                    "result": {
                        "success": True,
                        "result": "",
                        "output": f"hello\n{marker}{{\"value\": 3}}",
                        "log_output": [
                            {"type": "Info", "output": "hello"},
                            {"type": "Info", "output": f'{marker}{{"value": 3}}'},
                        ],
                    },
                }
            ]
        )
        dynamic_info = {59201: {"pid": 4321, "project_path": "Crowd.uproject"}}
        with patch.object(
            foreground_client, "_dynamic_bridge_ports", return_value=dynamic_info
        ), patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            result = foreground_client.execute_python(
                59201,
                "print('hello')\nresult = {'value': 3}",
                timeout=1,
            )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["bridge_protocol"], "native")
        self.assertEqual(result["result"], {"value": 3})
        self.assertEqual(result["stdout"], "hello")
        self.assertEqual(result["stderr"], "")
        self.assertEqual(result["traceback"], "")
        self.assertEqual(connection.sent[0]["method"], "execute_python")
        self.assertEqual(connection.sent[0]["params"].keys(), {"code"})
        self.assertIn(marker, connection.sent[0]["params"]["code"])

    def test_native_execute_python_returns_traceback(self):
        connection = FakeConnection(
            [
                {
                    "jsonrpc": "2.0",
                    "id": "request-id",
                    "result": {
                        "success": False,
                        "result": "Traceback (most recent call last):\nValueError: bad",
                        "output": "",
                        "log_output": [],
                    },
                }
            ]
        )
        with patch.object(
            foreground_client,
            "_dynamic_bridge_ports",
            return_value={59201: {"pid": 4321}},
        ), patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            result = foreground_client.execute_python(
                59201,
                "raise ValueError('bad')",
                timeout=1,
            )

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error_type"], "PythonError")
        self.assertIn("ValueError: bad", result["stderr"])
        self.assertIn("ValueError: bad", result["traceback"])

    def test_execute_python_returns_traceback(self):
        connection = FakeConnection([
            _ack(),
            {
                "type": "automation_response",
                "requestId": "request-id",
                "success": False,
                "error": "PYTHON_ERROR",
                "result": {
                    "output": "",
                    "error": "Traceback (most recent call last):\nValueError: bad",
                },
            },
        ])
        with patch.object(foreground_client, "connect", return_value=connection), patch.object(
            foreground_client.uuid, "uuid4", return_value=type("U", (), {"hex": "request-id"})()
        ):
            result = foreground_client.execute_python(8091, "raise ValueError('bad')", timeout=1)

        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error_type"], "PYTHON_ERROR")
        self.assertIn("ValueError: bad", result["traceback"])

    def test_timeout_is_structured(self):
        connection = FakeConnection([_ack()], error=TimeoutError("slow"))
        with patch.object(foreground_client, "connect", return_value=connection):
            result = foreground_client.execute_python(8091, "result = 1", timeout=0.01)
        self.assertEqual(result["status"], "TIMEOUT")
        self.assertEqual(result["error_type"], "TimeoutError")

    def test_disconnect_is_structured(self):
        with patch.object(foreground_client, "connect", side_effect=OSError("refused")):
            result = foreground_client.execute_python(8091, "result = 1", timeout=0.01)
        self.assertEqual(result["status"], "NEEDS_ATTENTION")
        self.assertEqual(result["error_type"], "ConnectionError")

    def test_discovery_deduplicates_same_ue_process(self):
        observed_timeouts = []

        def fake_handshake(port, timeout=foreground_client.DEFAULT_DISCOVERY_TIMEOUT):
            observed_timeouts.append(timeout)
            if port in (8090, 8091):
                return {**_ack(), "port": port}
            raise foreground_client.UEBridgeError("closed", "ConnectionError")

        with patch.object(foreground_client, "ue_port_range", return_value=range(8090, 8093)), patch.object(
            foreground_client, "_dynamic_bridge_ports", return_value={}
        ), patch.object(
            foreground_client, "handshake", side_effect=fake_handshake
        ), patch.object(
            foreground_client,
            "_listener_process_info",
            return_value={8090: {"pid": 101}, 8091: {"pid": 101}},
        ):
            sessions = foreground_client.discover_ue_sessions()

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["ports"], [8090, 8091])
        self.assertEqual(sessions[0]["port"], 8091)
        self.assertEqual(sessions[0]["bridge_protocol"], "legacy")
        self.assertEqual(observed_timeouts, [foreground_client.DEFAULT_DISCOVERY_TIMEOUT] * 3)

    def test_discovery_keeps_shared_legacy_session_ids_separate_by_pid(self):
        def fake_handshake(port, timeout=foreground_client.DEFAULT_DISCOVERY_TIMEOUT):
            return {**_ack("shared-session"), "port": port, "bridge_protocol": "legacy"}

        process_info = {
            8090: {"pid": 101, "process": "UnrealEditor.exe", "project_path": "A.uproject"},
            8091: {"pid": 202, "process": "UnrealEditor.exe", "project_path": "B.uproject"},
        }
        with patch.object(foreground_client, "ue_port_range", return_value=range(8090, 8092)), patch.object(
            foreground_client, "_dynamic_bridge_ports", return_value={}
        ), patch.object(foreground_client, "handshake", side_effect=fake_handshake), patch.object(
            foreground_client, "_listener_process_info", return_value=process_info
        ):
            sessions = foreground_client.discover_ue_sessions()

        self.assertEqual(len(sessions), 2)
        self.assertEqual([session["pid"] for session in sessions], [101, 202])
        self.assertEqual([session["port"] for session in sessions], [8090, 8091])
        self.assertTrue(all(session["bridge_protocol"] == "legacy" for session in sessions))

    def test_discovery_keeps_pidless_legacy_port_separate_from_pid_session(self):
        def fake_handshake(port, timeout=foreground_client.DEFAULT_DISCOVERY_TIMEOUT):
            return {**_ack("shared-session"), "port": port, "bridge_protocol": "legacy"}

        process_info = {
            8090: {"pid": 101, "process": "UnrealEditor.exe", "project_path": "A.uproject"},
        }
        with patch.object(foreground_client, "ue_port_range", return_value=range(8090, 8092)), patch.object(
            foreground_client, "_dynamic_bridge_ports", return_value={}
        ), patch.object(foreground_client, "handshake", side_effect=fake_handshake), patch.object(
            foreground_client, "_listener_process_info", return_value=process_info
        ):
            sessions = foreground_client.discover_ue_sessions()

        self.assertEqual(len(sessions), 2)
        self.assertEqual([session.get("pid") for session in sessions], [101, None])
        self.assertEqual([session["port"] for session in sessions], [8090, 8091])
        self.assertTrue(all(session["session_id"] == "shared-session" for session in sessions))
        self.assertTrue(all(session["bridge_protocol"] == "legacy" for session in sessions))

    def test_discovery_prefers_native_bridge_for_same_editor_pid(self):
        def fake_handshake(port, timeout=foreground_client.DEFAULT_DISCOVERY_TIMEOUT):
            if port == 8091:
                return {**_ack("shared-session"), "port": port}
            raise foreground_client.UEBridgeError("closed", "ConnectionError")

        native_ack = {
            "port": 59201,
            "bridge_protocol": "native",
            "serverName": "UE_MCP_Bridge",
            "serverVersion": "",
            "protocolVersion": "2.0",
            "buildStatus": {"success": True, "status": "idle"},
        }
        process_info = {
            8091: {"pid": 4321, "project_path": "Crowd.uproject"},
            59201: {"pid": 4321, "project_path": "Crowd.uproject"},
        }
        with patch.object(
            foreground_client, "ue_port_range", return_value=range(8091, 8092)
        ), patch.object(
            foreground_client,
            "_dynamic_bridge_ports",
            return_value={59201: process_info[59201]},
        ), patch.object(
            foreground_client, "handshake", side_effect=fake_handshake
        ), patch.object(
            foreground_client, "native_handshake", return_value=native_ack
        ), patch.object(
            foreground_client, "_listener_process_info", return_value=process_info
        ):
            sessions = foreground_client.discover_ue_sessions()

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["bridge_protocol"], "native")
        self.assertEqual(sessions[0]["port"], 59201)

    def test_discovery_skips_non_websocket_listener(self):
        with socketserver.TCPServer((foreground_client.HOST, 0), PlainHttpHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                with patch.object(
                    foreground_client, "ue_port_range", return_value=range(port, port + 1)
                ), patch.object(foreground_client, "_dynamic_bridge_ports", return_value={}):
                    sessions = foreground_client.discover_ue_sessions()
            finally:
                server.shutdown()
                thread.join(timeout=2)

        self.assertEqual(sessions, [])

    def test_execute_python_structures_non_websocket_listener_error(self):
        with socketserver.TCPServer((foreground_client.HOST, 0), PlainHttpHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = foreground_client.execute_python(
                    server.server_address[1],
                    "result = 1",
                    timeout=1,
                )
            finally:
                server.shutdown()
                thread.join(timeout=2)

        self.assertEqual(result["status"], "NEEDS_ATTENTION")
        self.assertEqual(result["error_type"], "ConnectionError")


if __name__ == "__main__":
    unittest.main()
