"""Public MCP contract coverage independent of a running DCC."""

import asyncio
import json
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolRequestParams
from pydantic import ValidationError

from cgi_pipeline.catalog import catalog_version
from cgi_pipeline.server import internals
from cgi_pipeline.server import server
from cgi_pipeline.server import tools_operations
from cgi_pipeline.server.mcp_audit import McpCallAuditMiddleware
from cgi_pipeline.server.models import (
    MAX_BATCH_CALLS,
    ExecuteApiInput,
    ExecuteBatchInput,
    ExecuteWorkflowInput,
)
from cgi_pipeline.server.tools_operations import EXPOSED_TOOLS
from cgi_pipeline.server.public_surface import (
    BASELINE_TOOLS,
    PUBLIC_TOOL_ROLES,
    PUBLIC_TOOL_ROUTES,
)


def test_public_surface_preserves_baseline_and_registered_metadata():
    assert BASELINE_TOOLS == {
        "list_apis",
        "api_help",
        "execute_api",
        "execute_batch",
        "get_task_result",
        "list_workflows",
        "pipeline_execute_workflow",
    }
    assert BASELINE_TOOLS <= EXPOSED_TOOLS
    assert set(PUBLIC_TOOL_ROUTES) == set(EXPOSED_TOOLS)
    assert set(PUBLIC_TOOL_ROLES) == set(EXPOSED_TOOLS)
    assert len(EXPOSED_TOOLS) < 40
    assert not any(
        name.startswith(("maya_", "blender_", "ue_", "houdini_"))
        for name in EXPOSED_TOOLS
    )


def test_api_and_workflow_wait_for_final_results_by_default():
    api_input = ExecuteApiInput(api_id="pipeline.session.discover")
    workflow_input = ExecuteWorkflowInput(workflow_id="example")
    assert api_input.wait is True
    assert workflow_input.wait is True
    assert api_input.poll_interval_sec == 0.25
    assert workflow_input.poll_interval_sec == 0.25
    assert api_input.expected_catalog_version is None


@pytest.mark.parametrize('kind', ['api', 'batch', 'workflow'])
def test_public_calls_forward_business_and_workspace_projects_separately(kind):
    class Capture:
        def tool(self, **metadata):
            def register(function):
                setattr(self, metadata['name'], function)
                return function
            return register
    mcp = Capture()
    tools_operations.register_operation_tools(mcp)
    values = dict(project='ysj', workspace_project='default', business_task_id='PL284', wait=False)
    if kind == 'api':
        params = ExecuteApiInput(api_id='maya.scene.selection.get', **values)
        function, owner, position = mcp.execute_api, '_submit_api_to_celery', 1
    elif kind == 'batch':
        params = ExecuteBatchInput(calls=[{'api_id': 'maya.scene.selection.get', 'params': {}}], **values)
        function, owner, position = mcp.execute_batch, '_submit_chain', 0
    else:
        params = ExecuteWorkflowInput(workflow_id='blender_to_maya_full_build', **values)
        function, owner, position = mcp.pipeline_execute_workflow, '_submit_workflow', 0
    with patch.object(tools_operations, owner, return_value={'task_id': 'fixture', 'status': 'SUBMITTED'}) as submit:
        result = asyncio.run(function(params))
    assert result['status'] == 'SUBMITTED'
    payload = submit.call_args.args[position]
    assert payload['project'] == 'ysj' and payload['workspace_project'] == 'default'
    assert payload['business_task_id'] == 'PL284'


def test_foreground_batch_uses_unique_id_and_complete_context(tmp_path):
    class _FakeMcp:
        def tool(self, **metadata):
            def capture(function):
                if metadata.get("name") == "execute_batch":
                    self.execute_batch = function
                return function
            return capture

    mcp = _FakeMcp()
    tools_operations.register_operation_tools(mcp)
    params = ExecuteBatchInput(
        calls=[{"api_id": "maya.scene.selection.get", "params": {}}],
        project="ysj",
        asset_name="hero",
        source_path="C:/hero.ma",
        execution_mode="foreground",
        foreground_port=7123,
        expected_catalog_version=catalog_version(),
    )
    with patch.object(internals, "AUDIT_DIR", tmp_path), patch(
        "cgi_pipeline.server.foreground_client.submit_foreground_batch_task",
        return_value={"status": "SUCCESS"},
    ) as submit:
        result = asyncio.run(mcp.execute_batch(params))

    assert result["status"] == "SUCCESS"
    assert result["catalog_version"] == catalog_version()
    payload = submit.call_args.args[0]
    assert payload["task_id"].startswith("batch-")
    raw_task_id = payload["task_id"].removeprefix("batch-")
    assert str(uuid.UUID(raw_task_id)) == raw_task_id
    assert payload["api_context"] == {
        "expected_catalog_version": catalog_version(),
        "execution_mode": "foreground",
        "foreground_port": 7123,
        "project": "ysj",
        "asset_name": "hero",
        "source_path": "C:/hero.ma",
    }


def test_execute_api_reuses_current_catalog_version_without_discovery():
    class _FakeMcp:
        def tool(self, **metadata):
            def capture(function):
                if metadata.get("name") == "execute_api":
                    self.execute_api = function
                return function
            return capture

    mcp = _FakeMcp()
    tools_operations.register_operation_tools(mcp)
    params = ExecuteApiInput(
        api_id="maya.scene.selection.get",
        expected_catalog_version=catalog_version(),
        wait=False,
    )
    with patch.object(
        tools_operations,
        "_submit_api_to_celery",
        return_value={"task_id": "api-direct", "status": "SUBMITTED"},
    ) as submit:
        result = asyncio.run(mcp.execute_api(params))

    assert result.pop('timings_ms')['dispatch_return_ms'] >= 0
    assert result == {
        "catalog_version": catalog_version(),
        "task_id": "api-direct",
        "status": "SUBMITTED",
    }
    submit.assert_called_once()
    assert submit.call_args.args[1]["context"]["expected_catalog_version"] == catalog_version()


def test_execute_api_foreground_submissions_do_not_block_each_other():
    """Different Maya ports must be able to submit concurrently in one MCP loop."""
    class _FakeMcp:
        def tool(self, **metadata):
            def capture(function):
                if metadata.get("name") == "execute_api":
                    self.execute_api = function
                return function
            return capture

    mcp = _FakeMcp()
    tools_operations.register_operation_tools(mcp)
    barrier = threading.Barrier(2)
    observed_ports = []

    def submit(_api_id, payload):
        observed_ports.append(payload["context"]["foreground_port"])
        barrier.wait(timeout=1)
        return {"task_id": f"api-{payload['context']['foreground_port']}", "status": "SUBMITTED"}

    params = [
        ExecuteApiInput(
            api_id="maya.scene.selection.get",
            execution_mode="foreground",
            foreground_port=7123,
            expected_catalog_version=catalog_version(),
            wait=False,
        ),
        ExecuteApiInput(
            api_id="maya.scene.selection.get",
            execution_mode="foreground",
            foreground_port=7124,
            expected_catalog_version=catalog_version(),
            wait=False,
        ),
    ]

    async def run_concurrent_calls():
        return await asyncio.wait_for(
            asyncio.gather(*(mcp.execute_api(item) for item in params)),
            timeout=2,
        )

    with patch.object(tools_operations, "_submit_api_to_celery", side_effect=submit):
        results = asyncio.run(run_concurrent_calls())

    assert sorted(observed_ports) == [7123, 7124]
    assert [result["status"] for result in results] == ["SUBMITTED", "SUBMITTED"]


def test_foreground_wait_is_async_dispatch_plus_capped_wait():
    """A waiting foreground call must not block past the client timeout (Maya can run an hour)."""
    class _FakeMcp:
        def tool(self, **metadata):
            def capture(function):
                if metadata.get("name") == "execute_api":
                    self.execute_api = function
                return function
            return capture

    mcp = _FakeMcp()
    tools_operations.register_operation_tools(mcp)
    seen = []

    def submit(_api_id, payload):
        seen.append(payload["task_id"])
        time.sleep(payload["params"]["seconds"])
        return {"task_id": payload["task_id"], "status": "SUCCESS", "scene_session": {"foreground_port": 7123}}

    def call(seconds):
        params = ExecuteApiInput(api_id="maya.scene.selection.get", execution_mode="foreground",
                                 foreground_port=7123, expected_catalog_version=catalog_version(),
                                 wait=True, wait_timeout_sec=1, params={"seconds": seconds})
        return asyncio.run(mcp.execute_api(params))

    with patch.object(tools_operations, "_submit_api_to_celery", side_effect=submit):
        quick, slow = call(0), call(1.5)

    # In time: the full synchronous result as before. Past the cap: PROGRESS with the same task_id.
    assert quick["status"] == "SUCCESS" and quick["scene_session"]["foreground_port"] == 7123
    assert slow["status"] == "PROGRESS" and slow["task_id"] == seen[1]
    assert "get_task_result" in slow["recovery_hint"]


def test_execute_api_rejects_stale_contract_before_submission():
    class _FakeMcp:
        def tool(self, **metadata):
            def capture(function):
                if metadata.get("name") == "execute_api":
                    self.execute_api = function
                return function
            return capture

    mcp = _FakeMcp()
    tools_operations.register_operation_tools(mcp)
    params = ExecuteApiInput(
        api_id="maya.scene.selection.get",
        expected_catalog_version="stale-version",
        wait=False,
    )
    with patch.object(tools_operations, "_submit_api_to_celery") as submit:
        result = asyncio.run(mcp.execute_api(params))

    assert result["status"] == "ERROR"
    assert result["error_code"] == "CATALOG_VERSION_STALE"
    assert result["catalog_version"] == catalog_version()
    assert "No DCC was started" in result["recovery_hint"]
    submit.assert_not_called()


def test_execute_batch_rejects_stale_contract_before_validation():
    class _FakeMcp:
        def tool(self, **metadata):
            def capture(function):
                if metadata.get("name") == "execute_batch":
                    self.execute_batch = function
                return function
            return capture

    mcp = _FakeMcp()
    tools_operations.register_operation_tools(mcp)
    params = ExecuteBatchInput(
        calls=[{"api_id": "not.a.real.api", "params": {}}],
        expected_catalog_version="stale-version",
    )
    with patch.object(tools_operations, "_submit_chain") as submit:
        result = asyncio.run(mcp.execute_batch(params))

    assert result["status"] == "ERROR"
    assert result["error_code"] == "CATALOG_VERSION_STALE"
    assert result["catalog_version"] == catalog_version()
    submit.assert_not_called()


def test_execute_batch_accepts_maximum_call_count():
    call = {"api_id": "maya.scene.selection.get", "params": {}}

    params = ExecuteBatchInput(calls=[call] * MAX_BATCH_CALLS)

    assert len(params.calls) == MAX_BATCH_CALLS


def test_execute_batch_rejects_call_count_above_limit():
    call = {"api_id": "maya.scene.selection.get", "params": {}}

    with pytest.raises(ValidationError) as exc_info:
        ExecuteBatchInput(calls=[call] * (MAX_BATCH_CALLS + 1))

    error = exc_info.value.errors()[0]
    assert error["loc"] == ("calls",)
    assert error["type"] == "too_long"


def _registered_operation_tool(name):
    class FakeMcp:
        def tool(self, **metadata):
            def capture(function):
                setattr(self, metadata["name"], function)
                return function
            return capture

    mcp = FakeMcp()
    tools_operations.register_operation_tools(mcp)
    return getattr(mcp, name)


@pytest.mark.parametrize("tool_name", ["execute_api", "execute_batch"])
@pytest.mark.parametrize("mode,port,error_code", [
    ("local", None, "SERVICE_EXECUTION_MODE_UNSUPPORTED"),
    ("invalid", None, "API_CONTRACT_ERROR"),
    ("", None, "API_CONTRACT_ERROR"),
    ("foreground", None, "FOREGROUND_PORT_REQUIRED"),
    ("foreground", 0, "FOREGROUND_PORT_REQUIRED"),
])
def test_service_execution_rejects_invalid_context_before_side_effects(tool_name, mode, port, error_code):
    if mode in ("invalid", ""):
        error_code = "BATCH_EXECUTION_MODE_INVALID" if tool_name == "execute_batch" else "API_CONTRACT_ERROR"
    common = {"execution_mode": mode, "foreground_port": port,
              "business_task_id": "PL270", "wait": True}
    if tool_name == "execute_api":
        params = ExecuteApiInput(api_id="maya.scene.selection.get", **common)
    else:
        params = ExecuteBatchInput(calls=[{"api_id": "maya.scene.selection.get"}], **common)

    with (
        patch.object(internals, "_bind_workspace", return_value=None) as api_workspace,
        patch.object(tools_operations, "_bind_workspace", return_value=None) as batch_workspace,
        patch.object(internals, "_append_submission_audit"),
        patch.object(internals, "_ensure_worker") as worker,
        patch("cgi_pipeline.core.service_manager.start_redis", return_value=False) as redis,
        patch.object(tools_operations, "_submit_chain") as chain,
        patch("cgi_pipeline.server.foreground_client.submit_foreground_task", return_value={"status": "SUCCESS"}) as foreground_api,
        patch("cgi_pipeline.server.foreground_client.submit_foreground_batch_task", return_value={"status": "SUCCESS"}) as foreground_batch,
    ):
        result = asyncio.run(_registered_operation_tool(tool_name)(params))

    assert result["status"] == "ERROR"
    assert result["error_code"] == error_code
    if mode == "local":
        assert "api.execute_local" in result["recovery_hint"]
    for side_effect in (api_workspace, batch_workspace, worker, redis, chain, foreground_api, foreground_batch):
        side_effect.assert_not_called()


@pytest.mark.parametrize("mode", ["background", "foreground"])
def test_valid_service_execution_retains_existing_dispatch_paths(mode):
    sent_tasks = []
    celery = SimpleNamespace(send_task=lambda *args, **kwargs: sent_tasks.append((args, kwargs)))
    payload = {"params": {}, "context": {"execution_mode": mode, "foreground_port": 7123}}
    with (
        patch.object(internals, "_bind_workspace", return_value=None),
        patch.object(internals, "_append_submission_audit"),
        patch.object(internals, "_ensure_worker", return_value=(True, "ok")),
        patch("cgi_pipeline.core.service_manager.start_redis", return_value=True),
        patch("cgi_pipeline.core.service_manager.get_celery_app", return_value=celery),
        patch("cgi_pipeline.server.foreground_client.submit_foreground_task", return_value={"status": "SUCCESS"}) as foreground,
    ):
        result = internals._submit_api_to_celery("maya.scene.selection.get", payload)

    assert payload["api_context"]["execution_mode"] == mode
    if mode == "foreground":
        assert result["status"] == "SUCCESS"
        foreground.assert_called_once_with(payload, sync=True)
        assert not sent_tasks
    else:
        assert result["status"] == "SUBMITTED"
        assert sent_tasks[0][0] == ("cgi_pipeline.core.tasks.execute_api_operation",)
        foreground.assert_not_called()


@pytest.mark.parametrize("tool_name", ["execute_api", "execute_batch"])
def test_service_execution_checks_the_registered_api_mode_before_dispatch(tool_name):
    api_id = "maya.rig.maya_deformation_inherit_skin"
    values = {"reference_rig_path": "reference.ma", "reference_group": "|Old|geo"}
    common = {"execution_mode": "foreground", "foreground_port": 7123}
    if tool_name == "execute_api":
        params = ExecuteApiInput(api_id=api_id, params=values, **common)
    else:
        params = ExecuteBatchInput(calls=[{"api_id": api_id, "params": values}], **common)
    with (
        patch.object(internals, "_bind_workspace", return_value=None) as api_workspace,
        patch.object(tools_operations, "_bind_workspace", return_value=None) as batch_workspace,
        patch.object(internals, "_append_submission_audit"),
        patch.object(tools_operations, "_submit_chain") as chain,
        patch("cgi_pipeline.server.foreground_client.submit_foreground_batch_task", return_value={"status": "SUCCESS"}) as foreground,
    ):
        result = asyncio.run(_registered_operation_tool(tool_name)(params))

    assert result["status"] == "ERROR"
    assert result["error_code"] == "API_CONTRACT_ERROR"
    if tool_name == "execute_batch":
        assert result["failed_index"] == 0
    for side_effect in (api_workspace, batch_workspace, chain, foreground):
        side_effect.assert_not_called()


def test_wait_for_task_checks_before_sleeping_when_already_terminal():
    terminal = {"task_id": "api-ready", "status": "CHAIN_SUCCESS"}
    with patch.object(tools_operations, "get_task_result", return_value=terminal), \
         patch.object(tools_operations.asyncio, "sleep") as sleep:
        result = asyncio.run(tools_operations._wait_for_task("api-ready", 1, 0.25))

    assert result == terminal
    sleep.assert_not_called()


@pytest.mark.parametrize("status", ["SUCCESS", "ERROR"])
def test_generic_result_reader_recovers_a_normalized_receipt(tmp_path, status):
    receipt = {
        "api_id": "pipeline.session.discover",
        "api_version": "1.0.0",
        "status": status,
        "input": {"code": "x" * 1200},
        "output": {"sessions": ["真实业务结果"]},
        "elapsed_sec": 0.1,
        "error_code": "",
        "error": "",
        "recovery_hint": "",
        "artifacts": [],
    }
    entries = [
        {"status": "STEP_" + status, "detail": json.dumps(receipt)},
        {"status": "CHAIN_SUCCESS" if status == "SUCCESS" else "ERROR", "detail": "done"},
    ]
    (tmp_path / "api-test.json").write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    with patch.object(internals, "AUDIT_DIR", tmp_path), \
         patch("cgi_pipeline.core.progress.get_latest_state", return_value=None), \
         patch("cgi_pipeline.core.service_manager.get_celery_app", side_effect=RuntimeError("offline")):
        result = internals.get_task_result("api-test")

    assert result["receipt"] == receipt
    assert len(result["entries"][0]["detail"]) < 1000


def test_result_reader_retains_batch_outputs_and_references_large_receipts(tmp_path):
    from cgi_pipeline.contracts import make_api_receipt
    import time
    receipts = [make_api_receipt("test.api", "1", "SUCCESS", time.perf_counter(), output={"value": value})
                for value in ("first", "大" * 70000, "last")]
    entries = [{"status": "STEP_SUCCESS", "detail": json.dumps(r, ensure_ascii=False)} for r in receipts]
    entries.append({"status": "CHAIN_SUCCESS", "detail": "done"})
    audit_path = tmp_path / "batch-test.json"
    audit_path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")
    with patch.object(internals, "AUDIT_DIR", tmp_path), \
         patch("cgi_pipeline.core.progress.get_latest_state", return_value=None), \
         patch("cgi_pipeline.core.service_manager.get_celery_app", side_effect=RuntimeError("offline")):
        result = internals.get_task_result("batch-test")
    rows = result["step_receipts"]
    assert rows[0]["receipt"] == receipts[0]
    assert rows[2]["receipt"] == receipts[2]
    reference = rows[1]["receipt_ref"]
    assert reference["audit_path"] == str(audit_path)
    assert reference["entry_index"] == 1
    assert reference["utf8_bytes"] > reference["inline_limit_bytes"]
    assert "receipt" not in result


@pytest.mark.parametrize('layout', ['bare', 'flat', 'legacy'])
def test_generic_result_reader_recovers_compact_workflow_summary(tmp_path, layout):
    info_dir = tmp_path / ".info"
    info_dir.mkdir()
    if layout == 'flat':
        report_path = info_dir / 'wf-summary_REPORT.md'
        manifest_path = info_dir / 'wf-summary_manifest.json'
    elif layout == 'legacy':
        operation = info_dir / 'operations' / 'wf-summary'
        operation.mkdir(parents=True)
        report_path, manifest_path = operation / 'REPORT.md', operation / 'manifest.json'
    else:
        report_path, manifest_path = tmp_path / 'REPORT.md', tmp_path / 'manifest.json'
    report_path.write_text("workflow report", encoding="utf-8")
    post_compare_path = info_dir / "asset_post_compare_result.json"
    post_compare_path.write_text(
        json.dumps({
            "schema_version": "compare_result.v1",
            "compare": {
                "total_issues": 1,
                "label_a": "tex",
                "label_b": "rig_post",
                "paired": [{}, {}],
                "only_a": [{}],
                "only_b": [],
                "merge_groups": [],
                "split_groups": [],
                "pairing_groups": [
                    {"action": "IDENTICAL"},
                    {"action": "ORIG_INJECT"},
                    {"action": "ORIG_INJECT"},
                ],
                "target_only_dags": [],
            },
        }),
        encoding="utf-8",
    )
    outputs = {
        "verify.output_path": str(post_compare_path),
        "save.output_path": str(tmp_path / "asset_v001.ma"),
    }
    manifest_path.write_text(
        json.dumps({
            "wf_id": "wf-summary",
            "workflow_id": "tex_to_rig_verify_and_sync",
            "status": "SUCCESS",
            "outputs": outputs,
            "reports": [str(report_path)],
        }),
        encoding="utf-8",
    )
    steps = [
        {"step": index, "api_id": f"api.{index}", "status": "SUCCESS"}
        for index in range(1, 13)
    ]
    with patch.object(
        internals,
        "_read_audit",
        return_value={
            "task_id": "wf-summary",
            "status": "WORKFLOW_SUCCESS",
            "detail": "3 segments completed",
            "entries": [],
            "report_path": str(report_path),
            "audit_path": str(tmp_path / "audit.json"),
        },
    ), patch.object(internals, "collect_workflow_steps", return_value=steps):
        result = internals.get_task_result("wf-summary")

    assert result["status"] == "WORKFLOW_SUCCESS"
    assert result["workflow_id"] == "tex_to_rig_verify_and_sync"
    assert result["steps"] == steps
    assert result["step_checklist"].count("✓") == 12
    assert result["report_path"] == str(report_path)
    assert result["manifest_path"] == str(manifest_path)
    assert result["outputs"] == outputs
    assert result["post_compare"] == {
        "path": str(post_compare_path),
        "schema_version": "compare_result.v1",
        "total_issues": 1,
        "labels": {"a": "tex", "b": "rig_post"},
        "counts": {
            "paired": 2,
            "only_a": 1,
            "only_b": 0,
            "merge_groups": 0,
            "split_groups": 0,
            "pairing_groups": 3,
            "target_only_dags": 0,
        },
        "actions": {"IDENTICAL": 1, "ORIG_INJECT": 2},
    }


@pytest.mark.parametrize('content', ['{truncated', '{"wf_id": "another-operation"}'])
def test_flat_workflow_manifest_refuses_corrupt_or_foreign_identity(tmp_path, content):
    from cgi_pipeline.core import workspace
    info = tmp_path / '.info'
    info.mkdir()
    report = info / 'wf-exact_REPORT.md'
    report.write_text('task-specific report', encoding='utf-8')
    (info / 'wf-exact_manifest.json').write_text(content, encoding='utf-8')
    (tmp_path / 'manifest.json').write_text(json.dumps({'wf_id': 'wf-exact'}), encoding='utf-8')
    with pytest.raises(workspace.WorkspaceError) as failure:
        internals._manifest_for_artifact('wf-exact', str(report))
    assert failure.value.code == 'WORKSPACE_STATE_INVALID'


def test_workflow_manifest_refuses_layout_conflict_even_with_matching_bare_manifest(tmp_path):
    from cgi_pipeline.core import workspace
    info = tmp_path / '.info'
    legacy = info / 'operations' / 'wf-exact'
    legacy.mkdir(parents=True)
    manifest = json.dumps({'wf_id': 'wf-exact'})
    (info / 'wf-exact_manifest.json').write_text(manifest, encoding='utf-8')
    (legacy / 'manifest.json').write_text(manifest, encoding='utf-8')
    root_report = tmp_path / 'REPORT.md'
    root_report.write_text('workspace report', encoding='utf-8')
    (tmp_path / 'manifest.json').write_text(manifest, encoding='utf-8')
    with pytest.raises(workspace.WorkspaceError) as failure:
        internals._manifest_for_artifact('wf-exact', str(root_report))
    assert failure.value.code == 'WORKSPACE_OPERATION_CONFLICT'


def test_batch_result_recovery_preserves_policy_and_nested_receipt_references(tmp_path):
    import time
    from cgi_pipeline.contracts import make_api_receipt
    receipts = [make_api_receipt('test.api', '1', status, time.perf_counter(),
                                output={'value': 'x' * size})
                for status, size in [('SUCCESS', 80000), ('ERROR', 1)]]
    batch = {'status': 'ERROR', 'completed': 2, 'requested': 3,
             'atomic': True, 'rolled_back': True}
    for nested in (False, True):
        entries = ([{'status': 'ERROR', 'detail': json.dumps({**batch, 'results': receipts})}]
                   if nested else [
                       *[{'status': 'STEP_' + r['status'], 'detail': json.dumps(r)} for r in receipts],
                       {'status': 'BATCH_RESULT', 'detail': json.dumps(batch)}])
        result = {'status': 'ERROR'}
        internals._attach_api_receipts(result, entries, tmp_path / 'audit.json')
        assert result['batch'] == batch
        rows = result['step_receipts']
        assert rows[1]['receipt'] == receipts[1]
        ref = rows[0]['receipt_ref']
        recovered = json.loads(entries[ref['entry_index']]['detail'])
        if nested:
            recovered = recovered['results'][ref['result_index']]
        assert recovered == receipts[0]


def test_server_instructions_document_long_workflow_recovery():
    instructions = server._build_instructions()
    assert all(name in instructions for name in EXPOSED_TOOLS)
    assert "registry-controlled" in instructions
    assert "capped below the client tool timeout" in instructions
    assert "PROGRESS keeps the task_id" in instructions
    assert "waits server-side" in instructions
    assert "do not resubmit" in instructions


def _audit_context(tool_name="list_apis", fastmcp_context=None, meta=None, arguments=None):
    return MiddlewareContext(
        message=CallToolRequestParams(name=tool_name, arguments=arguments, _meta=meta),
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


def test_mcp_audit_records_api_id_without_copying_other_arguments(tmp_path):
    audit_path = tmp_path / "mcp_calls.jsonl"
    middleware = McpCallAuditMiddleware(audit_path)

    async def call_next(_context):
        return ToolResult(structured_content={"status": "SUCCESS"})

    asyncio.run(middleware.on_call_tool(
        _audit_context(
            tool_name="api_help",
            arguments={
                "api_id": "maya.scene.selection.get",
                "source_path": "C:/private/scene.ma",
            },
        ),
        call_next,
    ))

    record = json.loads(audit_path.read_text(encoding="utf-8"))
    assert record["api_id"] == "maya.scene.selection.get"
    assert "source_path" not in record
    assert "arguments" not in record


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
