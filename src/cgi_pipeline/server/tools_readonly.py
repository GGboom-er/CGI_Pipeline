"""The four read-only public MCP tools for CGI discovery and recovery."""

from __future__ import annotations

from copy import deepcopy
from cgi_pipeline.server.http_lifecycle import lifecycle

from cgi_pipeline.contracts import Access, Effect, ExecutionMode, Executor, Mode, Surface
from cgi_pipeline.server.internals import get_task_result
from cgi_pipeline.server.models import ExecuteApiInput, TaskQueryInput
from cgi_pipeline.server.receipt_view import project_result


def execute_call_template(spec: dict, version: str) -> dict:
    """Project manifest inputs and the public model into an MCP arguments template."""
    values = {}
    bindings = []
    for name, definition in spec.get("inputs", {}).items():
        if "default" in definition:
            values[name] = deepcopy(definition["default"])
        elif definition.get("required"):
            values[name] = None
            bindings.append({"path": ["params", "params", name], **deepcopy(definition)})
    outer = ExecuteApiInput(api_id=spec["api_id"], expected_catalog_version=version).model_dump()
    outer["params"] = values
    if outer["execution_mode"] not in spec["execution_modes"]:
        outer["execution_mode"] = spec["execution_modes"][0]
    if outer['execution_mode'] == 'foreground':
        bindings.append({'path': ['params', 'foreground_port'], 'type': 'integer', 'required': True,
                         'description': 'Port of the verified target DCC session.'})
    return {
        "tool": "execute_api",
        "arguments": {"params": outer},
        "requires_binding": bindings,
        "usage": (
            "Bind each requires_binding path to real task data before passing arguments to the tool. "
            "Nulls at these paths are placeholders, not executable examples. Review preconditions "
            "and bind session/project/source context when required; defaults do not select a live session. "
            "Optional API inputs are described in inputs; outer fields come from the public tool model."
        ),
    }


def register_readonly_tools(mcp):
    """Register catalog discovery, help, task recovery, and workflow listing."""

    @mcp.tool(
        name="list_apis",
        annotations={
            "title": "List CGI APIs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def list_apis_tool(
        executor: Executor | None = None,
        capability: str | None = None,
        operation: str | None = None,
        stages: list[str] | None = None,
        targets: list[str] | None = None,
        access: Access | None = None,
        surfaces: list[Surface] | None = None,
        execution_modes: list[ExecutionMode] | None = None,
        modes: list[Mode] | None = None,
        effects: list[Effect] | None = None,
        search: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict:
        """Filter and page the concise API catalog without starting a DCC."""
        from cgi_pipeline.catalog import catalog_version, page_capabilities

        try:
            page = page_capabilities(
                executor=executor,
                capability=capability,
                operation=operation,
                stages=stages,
                targets=targets,
                access=access,
                surfaces=surfaces,
                execution_modes=execution_modes,
                modes=modes,
                effects=effects,
                search=search,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as exc:
            return {
                "status": "ERROR",
                "error_code": "INVALID_CATALOG_QUERY",
                "error": str(exc),
                "total": 0,
                "catalog_version": catalog_version(),
                "next_cursor": None,
                "apis": [],
                "recovery_hint": "Correct the filters/limit, or restart pagination without cursor.",
            }
        rows = page["apis"]
        return {
            "total": page["total"],
            "catalog_version": page["catalog_version"],
            "next_cursor": page["next_cursor"],
            "apis": [
                {
                    "api_id": row["api_id"],
                    "access": row["access"],
                    "modes": row["modes"],
                    "summary": row.get("help", {}).get("summary", ""),
                }
                for row in rows
            ],
            "usage": (
                "Rows are a selection index; filter by facets instead of reading them. "
                "For an unknown API, read api_help once for effects, inputs, verification "
                "and catalog_version. For a known contract, call execute_api directly with "
                "expected_catalog_version."
            ),
        }

    @mcp.tool(
        name="api_help",
        annotations={
            "title": "Show one CGI API contract",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def api_help_tool(api_id: str) -> dict:
        """Return inputs, preconditions, effects, outputs, and recovery for one API."""
        from cgi_pipeline.catalog import capability_help_with_version

        try:
            spec, version = capability_help_with_version(api_id)
            return {
                "catalog_version": version,
                **spec,
                "mcp_call_template": execute_call_template(spec, version),
            }
        except KeyError as exc:
            return {
                "status": "ERROR",
                "error_code": "API_NOT_FOUND",
                "error": str(exc),
                "recovery_hint": "Use list_apis to select a registered API.",
            }

    @mcp.tool(
        name="get_task_result",
        annotations={
            "title": "Get CGI task result",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def get_task_result_tool(params: TaskQueryInput) -> dict:
        """Recover a task without resubmitting. By default the server waits for a
        terminal state up to a client-safe cap (wait_sec=0 reads the current state),
        so one call replaces client polling. Results over 4 KiB are summarized
        (status, artifacts, errors, small output fields, receipt_ref); detail=True
        returns the full result. In full results, terminal receipt or step_receipts
        contain complete API data within a shared 64 KiB inline budget; a nested
        receipt_ref gives audit_path and zero-based parsed entry_index for larger
        data; read that original JSONL entry's detail, not the clipped display log.
        For a nested foreground batch, result_index selects detail.results[result_index].
        batch contains completion counts, atomic policy and Maya undo outcome.
        rolled_back does not cover files or other non-undoable side effects. Workflow
        terminals also include steps, outputs, report, and post-compare summary.
        """
        from cgi_pipeline.core.task_status import is_terminal

        result = await lifecycle.run_sync(get_task_result, params.task_id)
        status = result.get("status", "")
        # Unknown ids return at once instead of holding the call for the whole cap.
        if params.wait_sec != 0 and not is_terminal(status) and status not in ("NOT_FOUND", "UNKNOWN"):
            from cgi_pipeline.server.tools_operations import _wait_for_task
            result = await _wait_for_task(params.task_id, params.wait_sec, 0.5)
        return project_result(result, params.detail)

    @mcp.tool(
        name="list_workflows",
        annotations={
            "title": "List CGI workflows",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def list_workflows_tool() -> dict:
        """List workflow steps, external input contracts and bindable call templates; no DCC startup."""
        from cgi_pipeline.core.workflow_engine import list_workflows

        workflows = list_workflows()
        return {"count": len(workflows), "workflows": workflows}
