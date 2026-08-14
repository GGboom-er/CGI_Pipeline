"""The four read-only public MCP tools for CGI discovery and recovery."""

from __future__ import annotations

from cgi_pipeline.server.internals import get_task_result
from cgi_pipeline.server.models import TaskQueryInput


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
    async def list_apis_tool() -> dict:
        """Return the concise API catalog without starting a DCC."""
        from cgi_pipeline.catalog import list_capabilities

        rows = list_capabilities()
        return {
            "total": len(rows),
            "apis": [
                {
                    "api_id": row["api_id"],
                    "version": row["version"],
                    "executor": row["executor"],
                    "capability": row["capability"],
                    "operation": row["operation"],
                    "stages": row["stages"],
                    "targets": row["targets"],
                    "access": row["access"],
                    "effects": row["effects"],
                    "execution_modes": row["execution_modes"],
                    "surfaces": row["surfaces"],
                    "modes": row["modes"],
                    "summary": row.get("help", {}).get("summary", ""),
                }
                for row in rows
            ],
            "usage": "Select an API, read api_help, then call execute_api.",
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
        from cgi_pipeline.catalog import capability_help

        try:
            return capability_help(api_id)
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
        """Recover one submitted task without knowing which DCC executed it."""
        return get_task_result(params.task_id)

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
        """List registered API compositions without starting a DCC."""
        from cgi_pipeline.core.workflow_engine import list_workflows

        workflows = list_workflows()
        return {"count": len(workflows), "workflows": workflows}
