"""Exercise contract delivery across the actual FastMCP JSON boundary, without DCCs."""

import asyncio
from copy import deepcopy
from unittest.mock import patch

import jsonschema
import pytest
from fastmcp import Client
from pydantic import ValidationError

from cgi_pipeline.catalog import catalog_version, list_capabilities, page_capabilities
from cgi_pipeline.contracts import validate_params
from cgi_pipeline.server.models import ExecuteApiInput
from cgi_pipeline.server.tools_operations import register_operation_tools
from cgi_pipeline.server.tools_readonly import register_readonly_tools
from cgi_pipeline.server.server import PipelineMCP, _build_instructions


def make_server():
    mcp = PipelineMCP("contract-test")
    register_operation_tools(mcp)
    register_readonly_tools(mcp)
    return mcp


def test_unknown_help_preserves_structured_error_receipt():
    async def scenario():
        async with Client(make_server()) as client:
            result = await client.call_tool(
                "api_help", {"api_id": "missing.contract.test"}, raise_on_error=False)
            assert result.is_error
            assert result.structured_content["status"] == "ERROR"
            assert "missing.contract.test" in str(result.structured_content)
            assert result.content
    asyncio.run(scenario())


@pytest.mark.parametrize("status,is_error", [
    *[(status, True) for status in (
        "ERROR", "FAIL", "FAILED", "FAILURE", "BLOCKED", "SUBMIT_FAILED",
        "AUDIT_FAILED", "TIMEOUT", "CANCELLED", "CHAIN_ABORTED", "CHAIN_BLOCKED",
        "CHAIN_AUDIT_FAILED", "WORKFLOW_ERROR", "WORKFLOW_ABORTED", "WORKFLOW_AUDIT_FAILED",
        "STEP_ERROR", "STEP_BLOCKED", "STEP_AUDIT_FAILED", "CHAIN_ERROR")],
    *[(status, False) for status in (
        "SUCCESS", "PARTIAL", "NEEDS_ATTENTION", "PENDING", "STARTED",
        "RUNNING", "RETRY", "STEP_NEEDS_ATTENTION", None, {"unexpected": "shape"})],
])
def test_business_status_across_real_mcp_boundary(status, is_error):
    async def scenario():
        server = PipelineMCP("status-test")
        receipt = {"status": status, "task_id": "receipt-test", "audit_path": "audit.json",
                   "results": [{"status": "ERROR", "error": "nested-only"}]}

        @server.tool
        def status_probe() -> dict:
            return receipt

        async with Client(server) as client:
            result = await client.call_tool("status_probe", {}, raise_on_error=False)
            assert result.is_error is is_error
            assert result.structured_content == receipt
            assert result.content
    asyncio.run(scenario())


def test_instructions_explain_sandbox_exceptions():
    instructions = _build_instructions()
    assert "system auto-creates task sandbox" not in instructions
    assert "Existing UE editor and read-only Pipeline calls" in instructions
    assert "Foreground calls operate the selected session's existing scene" in instructions
    assert "protected paths remain read-only in every mode" in instructions
    assert 'Background saves stay inside the workspace' not in instructions
    assert 'not only the current run_dir' in instructions
    assert 'same OS directory lock' in instructions
    assert 'pipeline.runtime.status/release' in instructions


def test_fixed_facets_reject_invalid_values_but_valid_empty_search_succeeds():
    for filters in ({"executor": "unreal"}, {"access": "reading"}, {"surfaces": ["unreal"]}):
        with pytest.raises(ValueError, match="choose one of"):
            page_capabilities(**filters)
    assert page_capabilities(executor="ue", search="no-such-capability-ever")["total"] == 0
    with pytest.raises(ValidationError, match="Extra inputs"):
        ExecuteApiInput(api_id="pipeline.session.discover", typo=True)


def test_real_mcp_schema_help_execution_and_rejections():
    async def scenario():
        async with Client(make_server()) as client:
            tools = {tool.name: tool.inputSchema for tool in await client.list_tools()}
            enum = tools["list_apis"]["properties"]["executor"]["anyOf"][0]["enum"]
            assert enum == ["blender", "maya", "pipeline", "ue"]
            for args in ({"executor": "unreal"}, {"query": "ue"}, {"surfaces": ["unreal"]}):
                result = await client.call_tool("list_apis", args, raise_on_error=False)
                assert result.is_error, (args, result)
            help_result = await client.call_tool("api_help", {"api_id": "pipeline.session.discover"})
            template = help_result.data["mcp_call_template"]
            assert template["requires_binding"] == []
            arguments = template["arguments"]
            assert arguments["params"]["params"] == {"dcc": "all"}
            jsonschema.validate(arguments, tools[template["tool"]])
            with patch("cgi_pipeline.server.tools_operations._submit_api_to_celery",
                       return_value={"status": "SUCCESS", "marker": "submission-boundary"}) as submit:
                result = await client.call_tool(template["tool"], arguments)
                assert result.data["marker"] == "submission-boundary"
                assert submit.call_args.args[1]["params"] == {"dcc": "all"}
                submit.reset_mock()
                invalid_calls = [
                    arguments["params"],
                    {"params": {**arguments["params"], "typo": True}},
                    {**arguments, "typo": True},
                ]
                for invalid in invalid_calls:
                    result = await client.call_tool("execute_api", invalid, raise_on_error=False)
                    assert result.is_error, (invalid, result)
                stale = deepcopy(arguments)
                stale["params"]["expected_catalog_version"] = "stale"
                result = await client.call_tool("execute_api", stale, raise_on_error=False)
                assert result.is_error
                assert result.structured_content["error_code"] == "CATALOG_VERSION_STALE"
                submit.assert_not_called()

            # Bind the required UE inputs, then cross the same JSON boundary.
            # The submission function is replaced, so this never contacts a port.
            result = await client.call_tool("api_help", {"api_id": "ue.editor.python.execute"})
            template = result.data["mcp_call_template"]
            assert {tuple(item["path"]) for item in template["requires_binding"]} == {
                ("params", "params", "port"), ("params", "params", "code")}
            arguments = template["arguments"]
            assert arguments["params"]["params"]["port"] is None
            arguments["params"]["params"].update(port=1, code="result = 'isolated-test'")
            jsonschema.validate(arguments, tools[template["tool"]])
            validate_params(next(spec for spec in list_capabilities()
                                 if spec["api_id"] == "ue.editor.python.execute"),
                            arguments["params"]["params"])
            with patch("cgi_pipeline.server.tools_operations._submit_api_to_celery",
                       return_value={"status": "SUCCESS"}) as submit:
                await client.call_tool(template["tool"], arguments)
                assert submit.call_args.args[0] == "ue.editor.python.execute"
                assert submit.call_args.args[1]["params"]["port"] == 1
    asyncio.run(scenario())


def test_all_catalog_templates_match_real_public_schema_after_typed_binding():
    async def scenario():
        async with Client(make_server()) as client:
            tools = {tool.name: tool.inputSchema for tool in await client.list_tools()}
            for spec in list_capabilities():
                result = await client.call_tool("api_help", {"api_id": spec["api_id"]})
                template = result.data["mcp_call_template"]
                arguments = template["arguments"]
                for binding in template["requires_binding"]:
                    # Synthetic data is used only for schema checking, never execution.
                    kind = binding.get("type", "string")
                    if isinstance(kind, list):
                        kind = kind[0]
                    value = binding.get("choices", [None])[0]
                    if value is None:
                        value = {"string": "test", "str": "test", "integer": 1,
                                 "int": 1, "float": 1.0, "number": 1.0, "boolean": True,
                                 "bool": True, "object": {}, "dict": {}, "array": [], "list": []}[kind]
                    target = arguments
                    for name in binding["path"][:-1]:
                        target = target[name]
                    target[binding["path"][-1]] = value
                jsonschema.validate(arguments, tools[template["tool"]])
                validate_params(spec, arguments["params"]["params"])
                assert arguments["params"]["expected_catalog_version"] == catalog_version()
    asyncio.run(scenario())
