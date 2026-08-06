import json
import socket
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dccs.blender import foreground_client
from mcp_server import internals


class FakeBridge:
    def __init__(self, token="test-token"):
        self.token = token
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(1)
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.socket.close()
        self.thread.join(timeout=1)

    def _serve(self):
        try:
            connection, _address = self.socket.accept()
        except OSError:
            return
        with connection:
            request = json.loads(connection.recv(65536).split(b"\n", 1)[0])
            if request.get("token") != self.token:
                response = {
                    "id": request.get("id"),
                    "ok": False,
                    "error_type": "AuthenticationError",
                    "error": "bad token",
                }
            else:
                response = {
                    "id": request.get("id"),
                    "ok": True,
                    "result": {"status": "SUCCESS", "method": request.get("method")},
                }
            connection.sendall(json.dumps(response).encode("utf-8") + b"\n")


class BlenderForegroundBridgeTests(unittest.TestCase):
    def test_request_round_trip(self):
        with FakeBridge() as bridge:
            result = foreground_client.request(
                bridge.port,
                "bridge.health",
                token="test-token",
                timeout=1,
            )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["method"], "bridge.health")

    def test_wrong_token_is_explicit(self):
        with FakeBridge() as bridge:
            with self.assertRaises(foreground_client.BlenderBridgeError) as caught:
                foreground_client.request(bridge.port, "bridge.health", token="wrong", timeout=1)
        self.assertEqual(caught.exception.error_type, "AuthenticationError")

    def test_disconnected_port_is_explicit(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.assertRaises(foreground_client.BlenderBridgeError):
            foreground_client.request(port, "bridge.health", token="test-token", timeout=0.2)

    def test_blender_foreground_routes_to_blender_client(self):
        payload = {
            "parameters": {
                "execution_mode": "foreground",
                "foreground_port": 9876,
                "code": "result = 1",
            }
        }
        expected = {"status": "SUCCESS", "detail": {"outputs": {"result": 1}}}
        with patch(
            "dccs.blender.foreground_client.submit_foreground_task",
            return_value=expected,
        ) as submit:
            actual = internals._submit_to_celery("blender_exec_code", payload)
        self.assertEqual(actual, expected)
        submit.assert_called_once()

    def test_maya_foreground_route_is_unchanged(self):
        payload = {
            "parameters": {
                "execution_mode": "foreground",
                "foreground_port": 7009,
                "code": "result = 1",
            }
        }
        expected = {"status": "SUCCESS", "detail": {"outputs": {"result": 1}}}
        submit = Mock(return_value=expected)
        maya_client = types.ModuleType("mcp_server.foreground_client")
        maya_client.submit_foreground_task = submit
        with patch.dict(sys.modules, {"mcp_server.foreground_client": maya_client}):
            actual = internals._submit_to_celery("exec_code", payload)
        self.assertEqual(actual, expected)
        submit.assert_called_once()

    def test_blender_background_does_not_use_foreground_client(self):
        payload = {
            "parameters": {
                "execution_mode": "background",
                "code": "result = 1",
            }
        }
        with patch(
            "dccs.blender.foreground_client.submit_foreground_task",
            side_effect=AssertionError("foreground client must not run"),
        ), patch("core.service_manager.start_redis", return_value=False):
            actual = internals._submit_to_celery("blender_exec_code", payload)
        self.assertEqual(actual["status"], "SUBMIT_FAILED")
        self.assertIn("Redis", actual["error"])


if __name__ == "__main__":
    unittest.main()
