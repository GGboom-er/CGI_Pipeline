import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server import tools_operations
from mcp_server import server


class UEToolRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_lifespan_is_empty(self):
        """UE 已经是单层直连，MCP lifespan 不管理额外 sidecar。"""
        async with server._server_lifespan(server.mcp):
            pass

    async def test_native_session_goes_straight_to_websocket(self):
        """不论 bridge_protocol，一律直连 WebSocket 原生 method。"""
        session = {
            "port": 59201,
            "bridge_protocol": "native",
            "project_path": "S:/Project/Test/Test.uproject",
        }
        native_result = {"status": "SUCCESS", "result": {"editorConnected": True}}

        with patch(
            "dccs.ue.foreground_client.get_bridge_session", return_value=session
        ) as get_session, patch(
            "dccs.ue.foreground_client.execute_action",
            return_value=native_result,
        ) as native_call:
            result = await tools_operations._execute_ue_action(
                59201,
                "get_project_status",
                {"detailed": True},
                10,
            )

        get_session.assert_not_called()
        native_call.assert_called_once_with(
            59201,
            "get_project_status",
            {"detailed": True},
            10,
        )
        self.assertEqual(result, native_result)

    async def test_result_is_returned_undecorated(self):
        """结果原样返回，键由插件侧决定；_execute_ue_action 只做透传。"""
        native_result = {"status": "SUCCESS", "result": {"status": "idle"}}

        with patch(
            "dccs.ue.foreground_client.execute_action", return_value=native_result
        ) as native_call:
            result = await tools_operations._execute_ue_action(
                59201,
                "get_build_status",
                {},
                10,
            )

        native_call.assert_called_once_with(59201, "get_build_status", {}, 10)
        self.assertEqual(result, native_result)
        self.assertNotIn("bridge_protocol", result)
        self.assertNotIn("session", result)

    async def test_legacy_session_uses_native_socket(self):
        native_result = {"status": "SUCCESS", "result": {"nodeCount": 3}}

        with patch(
            "dccs.ue.foreground_client.execute_action", return_value=native_result
        ):
            result = await tools_operations._execute_ue_action(
                8091,
                "manage_blueprint",
                {"action": "get_nodes"},
                10,
            )

        self.assertEqual(result, native_result)


if __name__ == "__main__":
    unittest.main()
