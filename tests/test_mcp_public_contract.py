"""Public MCP contract coverage independent of a running DCC."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams

from cgi_pipeline.server import internals
from cgi_pipeline.server import tools_operations
from cgi_pipeline.server.mcp_audit import McpCallAuditMiddleware
from cgi_pipeline.server.models import ExecuteApiInput, ExecuteWorkflowInput
from cgi_pipeline.server.tools_operations import EXPOSED_TOOLS


def test_public_surface_has_exactly_six_generic_tools():
    assert EXPOSED_TOOLS == {
        "list_apis",
        "api_help",
        "execute_api",
        "get_task_result",
        "list_workflows",
        "pipeline_execute_workflow",
    }


def test_api_and_workflow_wait_for_final_results_by_default():
    api_input = ExecuteApiInput(api_id="pipeline.session.discover")
    workflow_input = ExecuteWorkflowInput(workflow_id="example")
    assert api_input.wait is True
    assert workflow_input.wait is True
    assert api_input.poll_interval_sec == 0.25
    assert workflow_input.poll_interval_sec == 0.25


def test_wait_for_task_checks_before_sleeping_when_already_terminal():
    terminal = {"task_id": "api-ready", "status": "CHAIN_SUCCESS"}
    with patch.object(tools_operations, "get_task_result", return_value=terminal), \
         patch.object(tools_operations.asyncio, "sleep") as sleep:
        result = asyncio.run(tools_operations._wait_for_task("api-ready", 1, 0.25))

    assert result == terminal
    sleep.assert_not_called()


def test_generic_result_reader_recovers_a_normalized_receipt():
    receipt = {
        "api_id": "pipeline.session.discover",
        "api_version": "1.0.0",
        "status": "SUCCESS",
        "input": {},
        "output": {"sessions": []},
        "elapsed_sec": 0.1,
        "error_code": "",
        "error": "",
        "recovery_hint": "",
        "artifacts": [],
    }
    with patch.object(
        internals,
        "_read_audit",
        return_value={"task_id": "api-test", "status": "SUCCESS", "detail": "done", "entries": [{"detail": __import__("json").dumps(receipt)}]},
    ):
        result = internals.get_task_result("api-test")

    assert result["receipt"] == receipt


def _audit_context(tool_name="list_apis", fastmcp_context=None, meta=None):
    return MiddlewareContext(
        message=CallToolRequestParams(name=tool_name, _meta=meta),
        fastmcp_context=fastmcp_context,
        method="tools/call",
    )


def test_mcp_audit_records_success_and_available_correlation(tmp_path):
    audit_path = tmp_path / "mcp_calls.jsonl"
    middleware = McpCallAuditMiddleware(audit_path)
    fastmcp_context = SimpleNamespace(
        request_id="request-1",
        session_id="session-1",
        client_id="antigravity",
        transport="streamable-http",
    )

    async def call_next(_context):
        return ToolResult(structured_content={"status": "SUCCESS", "total": 44})

    result = asyncio.run(
        middleware.on_call_tool(
            _audit_context(
                fastmcp_context=fastmcp_context,
                meta={"conversation_id": "conversation-1", "ignored": "secret"},
            ),
            call_next,
        )
    )

    record = json.loads(audit_path.read_text(encoding="utf-8"))
    assert result.structured_content["total"] == 44
    assert record["tool"] == "list_apis"
    assert record["status"] == "SUCCESS"
    assert record["request_id"] == "request-1"
    assert record["session_id"] == "session-1"
    assert record["client_id"] == "antigravity"
    assert record["correlation"] == {"conversation_id": "conversation-1"}
    assert isinstance(record["duration_ms"], float)


def test_mcp_audit_preserves_exception_and_missing_client_metadata(tmp_path):
    audit_path = tmp_path / "mcp_calls.jsonl"
    middleware = McpCallAuditMiddleware(audit_path)

    async def call_next(_context):
        raise RuntimeError("catalog unavailable")

    with pytest.raises(RuntimeError, match="catalog unavailable"):
        asyncio.run(middleware.on_call_tool(_audit_context(), call_next))

    record = json.loads(audit_path.read_text(encoding="utf-8"))
    assert record["status"] == "ERROR"
    assert record["error_type"] == "RuntimeError"
    assert record["request_id"] is None
    assert record["session_id"] is None
    assert record["client_id"] is None


def test_mcp_audit_rotates_to_a_bounded_number_of_files(tmp_path):
    audit_path = tmp_path / "mcp_calls.jsonl"
    middleware = McpCallAuditMiddleware(audit_path, max_bytes=1, backup_count=2)

    async def call_next(_context):
        return ToolResult(structured_content={"status": "SUCCESS"})

    for _ in range(4):
        asyncio.run(middleware.on_call_tool(_audit_context(), call_next))

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "mcp_calls.jsonl",
        "mcp_calls.jsonl.1",
        "mcp_calls.jsonl.2",
    ]
