"""UE is reached through catalog APIs, never a second named MCP tool path."""

from unittest.mock import patch

from cgi_pipeline.contracts import ApiContext
from cgi_pipeline.capabilities.ue.editor.action_invoke import handler as action_handler
from cgi_pipeline.capabilities.ue.editor.python_execute import handler as python_handler
from cgi_pipeline.server.tools_operations import EXPOSED_TOOLS


def _context(api_id: str) -> ApiContext:
    return ApiContext.from_mapping(
        {"_api_id": api_id, "_api_version": "1.0.0"}, "ue"
    )


def test_only_generic_mcp_tools_are_public():
    assert EXPOSED_TOOLS == {
        "list_apis",
        "api_help",
        "execute_api",
        "get_task_result",
        "list_workflows",
        "pipeline_execute_workflow",
    }


def test_native_ue_action_becomes_a_standard_api_receipt():
    bridge_result = {
        "status": "SUCCESS",
        "bridge_protocol": "jsonrpc",
        "session": {"port": 59201, "project_path": "S:/Test.uproject"},
        "result": {"status": "idle"},
        "progress": [],
        "error": "",
    }
    with patch("cgi_pipeline.hosts.ue.foreground_client.execute_action", return_value=bridge_result) as call:
        receipt = action_handler.execute(
            {"port": 59201, "method": "get_build_status", "arguments": {}, "timeout_seconds": 10},
            _context("ue.editor.action.invoke"),
        )

    call.assert_called_once_with(59201, "get_build_status", {}, 10)
    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["result"] == {"status": "idle"}
    assert receipt["output"]["session"]["port"] == 59201


def test_ue_python_error_preserves_structured_diagnostics():
    bridge_result = {
        "status": "ERROR",
        "bridge_protocol": "jsonrpc",
        "session": {"port": 59201},
        "result": None,
        "stdout": "before failure",
        "stderr": "Traceback: boom",
        "traceback": "Traceback: boom",
        "progress": [],
        "error_type": "PythonError",
        "error": "boom",
    }
    with patch("cgi_pipeline.hosts.ue.foreground_client.execute_python", return_value=bridge_result):
        receipt = python_handler.execute(
            {"port": 59201, "code": "raise RuntimeError('boom')", "description": "test", "timeout_seconds": 10},
            _context("ue.editor.python.execute"),
        )

    assert receipt["status"] == "ERROR"
    assert receipt["error_code"] == "PythonError"
    assert receipt["output"]["traceback"] == "Traceback: boom"
