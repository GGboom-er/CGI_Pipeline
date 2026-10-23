"""Public execution tools: atomic APIs, literal batches and workflows."""

from __future__ import annotations

import asyncio
import uuid
import time
from cgi_pipeline.server.http_lifecycle import lifecycle

from cgi_pipeline.server.internals import (
    _submit_api_to_celery,
    _submit_chain,
    _submit_workflow,
    _bind_workspace,
    _dispatch_foreground,
    _validate_service_execution,
    collect_workflow_steps,
    get_task_result,
    render_step_checklist,
)
from cgi_pipeline.server.models import ExecuteApiInput, ExecuteWorkflowInput
from cgi_pipeline.server.models import ExecuteBatchInput
from cgi_pipeline.server.public_surface import EXPOSED_TOOLS

WORKSPACE_FIELDS = ('workspace_id', 'business_task_id', 'run_dir', 'report_path',
                    'workspace_report_path', 'operation_dir', 'scene_session', 'runtime')


def _execution_state(submit, state):
    return {**{key: submit[key] for key in WORKSPACE_FIELDS if key in submit}, **state}


def _catalog_version_guard(expected: str | None, *, current_version: str | None = None) -> tuple[str, dict | None]:
    """Reject a stale cached contract before validation or DCC startup."""
    from cgi_pipeline.catalog import catalog_version

    current = catalog_version() if current_version is None else current_version
    if expected is not None and expected != current:
        return current, {
            "status": "ERROR",
            "error_code": "CATALOG_VERSION_STALE",
            "catalog_version": current,
            "error": f"cached catalog version {expected} does not match current version {current}",
            "recovery_hint": (
                "Call list_apis/api_help once, update the cached contract and catalog version, "
                "then submit again. No DCC was started."
            ),
        }
    return current, None


def _with_catalog_version(result: dict, version: str) -> dict:
    return {"catalog_version": version, **result}


async def prune_tools_to_whitelist(mcp):
    """Keep the public MCP surface limited to the catalog tools."""
    for tool in await mcp.list_tools():
        name = getattr(tool, "name", None)
        if name and name not in EXPOSED_TOOLS:
            mcp.local_provider.remove_tool(name)


async def _wait_for_task(task_id: str, timeout_sec: int, poll_interval_sec: float) -> dict:
    """Wait for one CGI queue task without blocking the MCP event loop."""
    from cgi_pipeline.core.task_status import is_terminal

    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        state = await lifecycle.run_sync(get_task_result, task_id)
        if is_terminal(state.get("status", "")):
            return state
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining > 0:
            await asyncio.sleep(min(poll_interval_sec, remaining))
    return {
        "task_id": task_id,
        "status": "PROGRESS",
        "message": f"已等待 {timeout_sec}s 未达终态，任务仍在后台执行。",
        "recovery_hint": "使用 get_task_result(task_id) 继续读取同一任务，不要重新提交。",
    }


def register_operation_tools(mcp):
    """Register catalog execution and workflow composition only."""

    @mcp.tool(
        name="execute_batch",
        annotations={"title": "Execute literal CGI API batch", "readOnlyHint": False,
                     "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    )
    async def execute_batch_tool(params: ExecuteBatchInput) -> dict:
        """Run literal calls in one DCC worker boundary; workflows remain for templates/cross-DCC steps."""
        from cgi_pipeline.catalog import snapshot
        from cgi_pipeline.contracts import ApiContractError, validate_params
        from cgi_pipeline.core.capability_registry import resolve_api_id
        selected_snapshot = snapshot()
        current_version, stale = _catalog_version_guard(
            params.expected_catalog_version, current_version=selected_snapshot[1])
        if stale:
            return stale

        execution_error = _validate_service_execution(
            params.execution_mode, params.foreground_port,
            invalid_mode_code="BATCH_EXECUTION_MODE_INVALID",
        )
        if execution_error:
            return _with_catalog_version(execution_error, current_version)

        normalized_calls = []
        executor = None
        for index, call in enumerate(params.calls):
            try:
                api_id = resolve_api_id(str(call.get("api_id") or ""), catalog_snapshot=selected_snapshot)
                spec = selected_snapshot[0][api_id]
                if spec.get('execution_strategy') == 'control':
                    raise ApiContractError('Control APIs require a standalone execute_api call.')
                if executor is None:
                    executor = spec["executor"]
                elif spec["executor"] != executor:
                    raise ApiContractError(
                        f"batch 不能混合 DCC executor：第 0 项为 {executor}，第 {index} 项为 {spec['executor']}"
                    )
                execution_error = _validate_service_execution(
                    params.execution_mode, params.foreground_port, spec=spec,
                )
                if execution_error:
                    if params.execution_mode == "foreground" and executor != "maya":
                        execution_error["error_code"] = "BATCH_FOREGROUND_UNSUPPORTED"
                    return _with_catalog_version({"failed_index": index, **execution_error}, current_version)
                values = validate_params(spec, call.get("params", call.get("api_params", {})) or {})
                normalized_calls.append({"api_id": api_id, "parameters": values})
            except (KeyError, ApiContractError, TypeError, ValueError) as exc:
                return _with_catalog_version({
                    "status": "ERROR", "error_code": "BATCH_CONTRACT_ERROR",
                    "failed_index": index, "error": str(exc),
                    "recovery_hint": "读取对应 api_help，修正 api_id、executor 或参数后重试；本次未启动 DCC。",
                }, current_version)
        if params.execution_mode == "foreground":
            if executor != "maya":
                return _with_catalog_version(
                    {"status": "ERROR", "error_code": "BATCH_FOREGROUND_UNSUPPORTED",
                     "error": "当前 batch 前台路径只支持 Maya。"},
                    current_version,
                )
            from cgi_pipeline.server.foreground_client import submit_foreground_batch_task
            payload = {
                "task_id": f"batch-{uuid.uuid4()}", "api_chain": normalized_calls,
                "workspace_project": params.workspace_project,
                "project": params.project, "asset_name": params.asset_name,
                "source_path": params.source_path,
                "workspace_id": params.workspace_id,
                "business_task_id": params.business_task_id,
                "scene_action": params.scene_action,
                "api_context": {
                    "expected_catalog_version": current_version,
                    "execution_mode": "foreground",
                    "foreground_port": params.foreground_port,
                    "project": params.project,
                    "asset_name": params.asset_name,
                    "source_path": params.source_path,
                },
                "atomic": params.atomic,
            }
            error = await lifecycle.run_sync(_bind_workspace, payload, 'batch', foreground=True)
            if error:
                return _with_catalog_version(error, current_version)
            result = await lifecycle.run_sync(_dispatch_foreground, submit_foreground_batch_task, payload, wait=params.wait)
            return _with_catalog_version(result, current_version)
        submit = await lifecycle.run_sync(_submit_chain, {
            "workspace_project": params.workspace_project,
            "workspace_id": params.workspace_id,
            "business_task_id": params.business_task_id,
            "scene_action": params.scene_action,
            "source_path": params.source_path, "project": params.project,
            "asset_name": params.asset_name,
            "api_chain": normalized_calls,
            "api_context": {"expected_catalog_version": current_version},
            "batch_mode": True, "atomic": params.atomic,
        })
        if not params.wait or submit.get("status") != "SUBMITTED":
            return _with_catalog_version(submit, current_version)
        result = await _wait_for_task(
            submit["task_id"], params.wait_timeout_sec, params.poll_interval_sec,
        )
        return _with_catalog_version(_execution_state(submit, result), current_version)

    @mcp.tool(
        name="execute_api",
        annotations={
            "title": "Execute one CGI API",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def execute_api_tool(params: ExecuteApiInput) -> dict:
        """Run one manifest-backed API and return its final receipt by default.

        Read api_help when selecting an unknown API or after a stale-contract
        response. For a known contract, pass expected_catalog_version and execute
        directly. Set wait=False for intentional asynchronous submission;
        get_task_result then resumes the same task without resubmission.
        """
        current_version, stale = _catalog_version_guard(params.expected_catalog_version)
        if stale:
            return stale

        started = time.perf_counter()
        submit = await lifecycle.run_sync(
            _submit_api_to_celery,
            params.api_id,
            {
                "workspace_project": params.workspace_project,
                "project": params.project,
                "workspace_id": params.workspace_id,
                "business_task_id": params.business_task_id,
                "scene_action": params.scene_action,
                "asset_name": params.asset_name,
                "source_path": params.source_path,
                "params": params.params,
                "context": {
                    "sync": params.wait,
                    "expected_catalog_version": current_version,
                    "execution_mode": params.execution_mode,
                    "foreground_port": params.foreground_port,
                    "project": params.project,
                    "asset_name": params.asset_name,
                    "source_path": params.source_path,
                },
            },
        )
        submit['timings_ms'] = {'dispatch_return_ms': (time.perf_counter() - started) * 1000}
        if not params.wait or submit.get("status") != "SUBMITTED":
            return _with_catalog_version(submit, current_version)

        wait_started = time.perf_counter()
        state = await _wait_for_task(
            submit["task_id"], params.wait_timeout_sec, params.poll_interval_sec,
        )
        state = _execution_state(submit, state)
        state['timings_ms'] = {**submit['timings_ms'], 'recovery_wait_ms': (time.perf_counter() - wait_started) * 1000}
        receipt = state.get("receipt")
        if receipt:
            return _with_catalog_version({
                "task_id": submit["task_id"],
                "api_id": params.api_id,
                "status": receipt["status"],
                "worker_status": state.get("status"),
                "receipt": receipt,
                **{
                    key: state[key]
                    for key in (*WORKSPACE_FIELDS, "audit_path", "timings_ms")
                    if key in state
                },
            }, current_version)
        return _with_catalog_version(state, current_version)

    @mcp.tool(
        name="pipeline_execute_workflow",
        annotations={
            "title": "Execute one CGI workflow",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def execute_workflow_tool(params: ExecuteWorkflowInput) -> dict:
        """Run a registered workflow using its declared runtime lifecycle.

        wait=True is the default and returns the final step checklist and report.
        For a workflow likely to exceed the client's tool timeout, use
        wait=False, retain the returned task_id, then call get_task_result with
        that id until terminal. A client timeout is not a reason to resubmit.
        """
        submit = await lifecycle.run_sync(_submit_workflow,
            {
                "workspace_project": params.workspace_project,
                "workflow_id": params.workflow_id,
                "workspace_id": params.workspace_id,
                "business_task_id": params.business_task_id,
                "scene_action": params.scene_action,
                "source_path": params.source_path,
                "project": params.project,
                "asset_name": params.asset_name,
                "extra_params": params.extra_params or {},
            }
        )
        if not params.wait or submit.get("status") != "SUBMITTED":
            return submit

        task_id = submit["task_id"]
        state = await _wait_for_task(
            task_id, params.wait_timeout_sec, params.poll_interval_sec,
        )
        state = _execution_state(submit, state)
        if state.get("status") == "PROGRESS":
            state["workflow_id"] = params.workflow_id
            return state

        steps = await lifecycle.run_sync(collect_workflow_steps, task_id)
        final_status = state.get("status", "UNKNOWN")
        result = {
            "task_id": task_id,
            "workflow_id": params.workflow_id,
            "status": final_status,
            "steps": steps,
            "step_checklist": render_step_checklist(steps),
        }
        for key in (*WORKSPACE_FIELDS, "audit_path", "error_code", "error", "failed_segment",
                    "step_receipts", "receipt", "receipt_ref", "terminal",
                    "effective_parameters", "save_requested", "result_saved", "saved_results"):
            if key in state:
                result[key] = state[key]
        if final_status not in {"WORKFLOW_SUCCESS", "SUCCESS", "CHAIN_SUCCESS"}:
            result["recovery_hint"] = (
                f"工作流未成功({final_status})。查看 step_checklist 和 report_path 定位。"
            )
        return result
