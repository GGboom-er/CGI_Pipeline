"""Single source of truth for the approved public MCP facade."""

from __future__ import annotations


PUBLIC_TOOL_ROUTES = {
    "list_apis": "enumerate atomic APIs",
    "api_help": "read one manifest-backed API contract",
    "execute_api": "run one atomic API and return or recover its receipt",
    "execute_batch": "run literal same-DCC API calls in one session boundary",
    "get_task_result": "recover one submitted API or workflow task",
    "list_workflows": "enumerate registered multi-step workflows",
    "pipeline_execute_workflow": "run one registered multi-step workflow",
}

PUBLIC_TOOL_ROLES = {
    "list_apis": "catalog",
    "api_help": "catalog",
    "execute_api": "execution",
    "execute_batch": "execution",
    "get_task_result": "task",
    "list_workflows": "workflow",
    "pipeline_execute_workflow": "workflow",
}

# These are the compatibility baseline, not a permanent maximum. A new facade
# must be added to both registries above with a distinct public interaction role.
BASELINE_TOOLS = frozenset({
    "list_apis",
    "api_help",
    "execute_api",
    "execute_batch",
    "get_task_result",
    "list_workflows",
    "pipeline_execute_workflow",
})
EXPOSED_TOOLS = frozenset(PUBLIC_TOOL_ROUTES)


def render_public_tool_routes() -> str:
    return "\n".join(
        f"- {name} -> {description}"
        for name, description in PUBLIC_TOOL_ROUTES.items()
    )
