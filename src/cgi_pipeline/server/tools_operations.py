"""The two mutating public MCP tools: atomic APIs and workflows."""

from __future__ import annotations

import asyncio

from cgi_pipeline.server.internals import (
    _submit_api_to_celery,
    _submit_workflow,
    collect_workflow_steps,
    get_task_result,
    render_step_checklist,
)
from cgi_pipeline.server.models import ExecuteApiInput, ExecuteWorkflowInput


EXPOSED_TOOLS = {
    "list_apis",
    "api_help",
    "execute_api",
    "get_task_result",
    "list_workflows",
    "pipeline_execute_workflow",
}


async def prune_tools_to_whitelist(mcp):
    """Keep the public MCP surface limited to the six catalog tools."""
    for tool in await mcp.list_tools():
        name = getattr(tool, "name", None)
        if name and name not in EXPOSED_TOOLS:
            mcp.local_provider.remove_tool(name)


async def _wait_for_task(task_id: str, timeout_sec: int, poll_interval_sec: float) -> dict:
    """Wait for one CGI queue task without blocking the MCP event loop."""
    from cgi_pipeline.core.task_status import is_terminal

    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        state = await asyncio.to_thread(get_task_result, task_id)
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

        Use api_help first when selecting an API or its parameters. Set
        wait=False only for intentional queue submission; get_task_result then
        resumes the same task without resubmission.
        """
        submit = _submit_api_to_celery(
            params.api_id,
            {
                "project": params.project,
                "asset_name": params.asset_name,
                "source_path": params.source_path,
                "params": params.params,
                "context": {
                    "execution_mode": params.execution_mode,
                    "foreground_port": params.foreground_port,
                    "project": params.project,
                    "asset_name": params.asset_name,
                    "source_path": params.source_path,
                },
            },
        )
        if not params.wait or submit.get("status") != "SUBMITTED":
            return submit

        state = await _wait_for_task(
            submit["task_id"], params.wait_timeout_sec, params.poll_interval_sec,
        )
        receipt = state.get("receipt")
        if receipt:
            return {
                "task_id": submit["task_id"],
                "api_id": params.api_id,
                "status": receipt["status"],
                "worker_status": state.get("status"),
                "receipt": receipt,
                **{
                    key: state[key]
                    for key in ("report_path", "audit_path")
                    if key in state
                },
            }
        return state

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
        """Run a registered multi-step workflow through the same CGI queue.

        wait=True is the default and returns the final step checklist and report.
        wait=False returns a recoverable task id for an intentional async run.
        """
        submit = _submit_workflow(
            {
                "workflow_id": params.workflow_id,
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
        if state.get("status") == "PROGRESS":
            state["workflow_id"] = params.workflow_id
            return state

        steps = await asyncio.to_thread(collect_workflow_steps, task_id)
        final_status = state.get("status", "UNKNOWN")
        result = {
            "task_id": task_id,
            "workflow_id": params.workflow_id,
            "status": final_status,
            "steps": steps,
            "step_checklist": render_step_checklist(steps),
        }
        for key in ("report_path", "audit_path"):
            if state.get(key):
                result[key] = state[key]
        if final_status not in {"WORKFLOW_SUCCESS", "SUCCESS", "CHAIN_SUCCESS"}:
            result["recovery_hint"] = (
                f"工作流未成功({final_status})。查看 step_checklist 和 report_path 定位。"
            )
        return result
