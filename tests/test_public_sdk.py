"""Behavior contract for CGI's host-facing SDK."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastmcp.client.client import CallToolResult
from fastmcp.exceptions import ToolError

from cgi_pipeline import api, client, tools
from cgi_pipeline.contracts import ApiContext
from cgi_pipeline.execution import execute_batch
from cgi_pipeline.server.server import PipelineMCP


def test_catalog_can_filter_by_independent_facets():
    rows = api.list(executor="maya", stages=["rigging"], access="read")
    ids = {row["api_id"] for row in rows}
    assert "maya.rig.skin.query_weights" in ids


def test_manifest_mode_bindings_resolve_without_changing_business_selectors():
    from cgi_pipeline.catalog import list_capabilities
    from cgi_pipeline.contracts import make_api_receipt

    # Spot checks for long-standing bindings; the full expectation comes from each manifest.
    known = {
        "maya.animation.curves.manage": {"query": "inspect", "set_tangent_types": "apply"},
        "maya.animation.expressions.manage": {"list": "inspect", "create": "apply", "update": "apply", "delete": "apply"},
        "maya.animation.keyframes.manage": {"query": "inspect", "set": "apply", "delete": "apply"},
        "maya.animation.poses.manage": {"list": "inspect", "save": "apply", "load": "apply", "mirror": "apply"},
        "maya.animation.time.manage": {"query": "inspect", "set_current": "apply", "set_range": "apply"},
        "maya.animation.retarget.transfer": {True: "preview", False: "apply"},
        "maya.rig.driven_keys.manage": {"create": "apply", "query": "inspect", "remove": "apply"},
        "maya.rig.maya_deformation_inherit_skin": {"diagnose_only": "inspect", "apply_skin": "apply"},
        "maya.rig.reference.update": {"preview": "preview", "apply": "apply"},
        "maya.rig.skeleton.normalize_scales": {True: "inspect", False: "apply"},
        "maya.asset.maya_master_cleanup": {"check": "inspect", "fix": "apply"},
    }
    # Host-local execution covers Maya-executed APIs; pipeline-executed bindings have their own tests.
    bound = {row["api_id"]: row for row in list_capabilities()
             if "mode_binding" in row and row.get("executor") == "maya"}
    expected = {api_id: {case["value"]: case["mode"] for case in spec["mode_binding"]["cases"]}
                for api_id, spec in bound.items()}
    assert set(known) <= set(bound)
    assert {api_id: expected[api_id] for api_id in known} == known
    for api_id, spec in bound.items():
        selector = spec["mode_binding"]["input"]
        base = {}
        for name, definition in spec["inputs"].items():
            if definition.get("required"):
                base[name] = {"string": "probe", "array": [], "object": {}, "integer": 0, "number": 0.0,
                              "boolean": False}[definition["type"]]
        for value, mode in expected[api_id].items():
            observed = []

            def handler(params, context):
                observed.append((params[selector], context.extras["_api_mode"]))
                return make_api_receipt(api_id, spec["version"], "SUCCESS", 0.0)

            with patch("cgi_pipeline.execution.load_handler", return_value=handler):
                result = api.execute_local(api_id, {**base, selector: value}, host="maya")
            assert result["status"] == "SUCCESS", result
            assert observed == [(value, mode)]
            assert type(observed[0][0]) is type(value)


@pytest.mark.parametrize("api_id,params", [
    ("maya.rig.skeleton.normalize_scales", {"nodes": ["joint1"], "dry_run": "false"}),
    ("maya.rig.skeleton.normalize_scales", {"nodes": ["joint1"], "dry_run": 0}),
    ("maya.animation.retarget.transfer", {"mapping": {}, "frames": [1], "dry_run": 1}),
    ("maya.asset.maya_master_cleanup", {"mode": "anything_else"}),
])
def test_invalid_mode_selector_is_rejected_before_loading_handler(api_id, params):
    with patch("cgi_pipeline.execution.load_handler") as load:
        result = api.execute_local(api_id, params, host="maya")
    assert result["error_code"] == "API_CONTRACT_ERROR"
    load.assert_not_called()


def test_cleanup_rejects_the_unimplemented_save_path_before_loading_handler():
    spec = api.help('maya.asset.maya_master_cleanup')
    manifest = yaml.safe_load((Path(__file__).resolve().parents[1] / 'src/cgi_pipeline/capabilities/maya/asset/'
                               'maya_master_cleanup/capability.yaml').read_text(encoding='utf-8'))
    assert spec['version'] == str(manifest['version'])
    assert 'save_path' not in spec['inputs']
    with patch('cgi_pipeline.execution.load_handler') as load:
        result = api.execute_local(spec['api_id'], {'mode': 'fix', 'save_path': 'unused.ma'}, host='maya')
    assert result['error_code'] == 'API_CONTRACT_ERROR'
    load.assert_not_called()


def test_single_mode_does_not_confuse_business_operation_with_intent():
    from cgi_pipeline.catalog import get_capability
    from cgi_pipeline.contracts import make_api_receipt
    from cgi_pipeline.execution import execute_api

    spec = get_capability("maya.scene.selection.set")
    spec["inputs"] = {"operation": {"type": "string", "required": True, "choices": ["add", "remove"]}}
    seen = []

    def handler(params, context):
        seen.append((params["operation"], context.extras["_api_mode"]))
        return make_api_receipt(spec["api_id"], spec["version"], "SUCCESS", 0.0)

    with patch("cgi_pipeline.execution.load_handler", return_value=handler):
        result = execute_api(spec["api_id"], {"operation": "add"},
                             catalog_snapshot=({spec["api_id"]: spec}, "test-version"))
    assert result["status"] == "SUCCESS"
    assert seen == [("add", "apply")]


def test_execute_local_rejects_a_service_only_capability():
    receipt = api.execute_local(
        "pipeline.session.discover",
        {"dcc": "maya"},
        host="maya",
    )
    assert receipt["status"] == "ERROR"
    assert receipt["error_code"] == "HOST_SURFACE_UNSUPPORTED"


def test_execute_local_runs_a_maya_handler_with_one_receipt_contract():
    class FakeCmds:
        def ls(self, _value, **kwargs):
            if kwargs.get("type") == "mesh":
                return ["|geo|bodyShape"]
            if kwargs.get("type") == "skinCluster":
                return ["body_skinCluster"]
            if kwargs.get("long"):
                return {
                    "bodyShape": ["|geo|bodyShape"],
                    "jointA": ["|rig|jointA"],
                    "jointB": ["|rig|jointB"],
                }.get(_value, [])
            return []

        def listHistory(self, _shape, **_kwargs):
            return ["body_skinCluster"]

        def skinCluster(self, _cluster, **_kwargs):
            return ["jointA", "jointB"]

        def polyEvaluate(self, _shape, **_kwargs):
            return 1

        def skinPercent(self, _cluster, _component, **_kwargs):
            return [0.75, 0.25]

    receipt = api.execute_local(
        "maya.rig.skin.query_weights",
        {
            "mesh": "bodyShape",
            "vertices": [0],
            "influence": "",
            "minimum_weight": 0.0,
            "max_vertices": 5,
        },
        host="maya",
        host_modules={"cmds": FakeCmds()},
    )
    assert receipt["status"] == "SUCCESS"
    assert receipt["api_id"] == "maya.rig.skin.query_weights"
    assert "output" in receipt
    assert "outputs" not in receipt


def test_tools_open_uses_the_registered_entrypoint():
    with patch("cgi_pipeline.tools._load_entrypoint", return_value=lambda: "opened") as load:
        assert tools.open("adpose", host="maya") == "opened"
    load.assert_called_once_with("adpose", "maya")


def test_execute_batch_runs_literal_calls_and_aborts_atomically():
    seen = []
    class FakeCmds:
        def undoInfo(self, **kwargs): seen.append(("undoInfo", kwargs))
        def undo(self): seen.append(("undo", {}))
    with patch("cgi_pipeline.execution.execute_api", side_effect=[
        {"status": "SUCCESS", "api_id": "a"},
        {"status": "ERROR", "api_id": "b"},
    ]):
        result = execute_batch(
            [{"api_id": "a", "params": {}}, {"api_id": "b", "params": {}},
             {"api_id": "c", "params": {}}],
            context={"cmds_module": FakeCmds()}, atomic=True,
        )
    assert result["status"] == "ERROR"
    assert result["completed"] == 2
    assert result["rolled_back"] is True
    assert any(name == "undo" for name, _ in seen)


def test_batch_context_uses_each_transport_step_id_without_mutating_parent():
    context = {'task_id': 'batch-parent', 'run_dir': 'sandbox'}
    with patch('cgi_pipeline.execution.execute_api', return_value={'status': 'SUCCESS'}) as execute:
        execute_batch([{'api_id': 'a', 'task_id': 'parent_s0'},
                       {'api_id': 'b', 'task_id': 'parent_s1'}], context=context)
    assert [call.args[2]['task_id'] for call in execute.call_args_list] == ['parent_s0', 'parent_s1']
    assert context == {'task_id': 'batch-parent', 'run_dir': 'sandbox'}


@pytest.mark.parametrize("data", [None, {}, False, 0])
def test_service_client_preserves_structured_receipts_and_falsey_data(data):
    receipt = {"status": "ERROR", "error_code": "API_CONTRACT_ERROR"}
    result = CallToolResult(content=[], structured_content=receipt, meta=None, data=data)

    class FakeClient:
        def __init__(self, _endpoint):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            pass

        async def call_tool(self, name, arguments, *, raise_on_error=True):
            assert name == "execute_api"
            assert arguments["params"]["api_id"] == "test.api"
            return result

    with patch("fastmcp.Client", FakeClient):
        received = asyncio.run(client.execute("test.api"))

    assert received is (receipt if data is None else data)


@pytest.mark.parametrize("status", ["SUCCESS", "ERROR", "BLOCKED", "SUBMIT_FAILED"])
def test_service_client_uses_real_pipeline_mcp_receipt_contract(status):
    service = PipelineMCP("SDK receipt contract")
    expected = {"status": status, "error_code": "API_CONTRACT_ERROR" if status != "SUCCESS" else ""}

    @service.tool(name="execute_api")
    def execute_api(params: dict) -> dict:
        return expected

    receipt = asyncio.run(client.execute("test.api", endpoint=service))
    assert receipt == expected


def test_service_client_preserves_real_pipeline_mcp_tool_errors():
    service = PipelineMCP("SDK tool error contract")

    @service.tool(name="execute_api")
    def execute_api(params: dict) -> dict:
        raise ValueError("transport tool failed")

    with pytest.raises(ToolError, match="transport tool failed"):
        asyncio.run(client.execute("test.api", endpoint=service))


def test_service_client_does_not_catch_connection_errors():
    with patch("fastmcp.Client", side_effect=RuntimeError("connection unavailable")):
        with pytest.raises(RuntimeError, match="connection unavailable"):
            asyncio.run(client.execute("test.api"))
