"""Lazy network client for CGI's MCP service."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from .core.task_status import is_failure


DEFAULT_ENDPOINT = "http://127.0.0.1:8000/mcp"


async def execute(api_id: str, params: Mapping[str, Any] | None = None, *,
                  endpoint: str = DEFAULT_ENDPOINT, project: str = "default",
                  asset_name: str = "untitled", source_path: str = "",
                  execution_mode: str = "background", foreground_port: int | None = None,
                  workspace_id: str | None = None, scene_action: str = "continue",
                  workspace_project: str | None = None,
                  wait: bool = True) -> Any:
    from fastmcp import Client
    from fastmcp.exceptions import ToolError
    from mcp.types import TextContent

    payload = {
        "api_id": api_id, "params": dict(params or {}), "project": project,
        "asset_name": asset_name, "source_path": source_path,
        "execution_mode": execution_mode, "foreground_port": foreground_port,
        "wait": wait,
        "workspace_id": workspace_id, "scene_action": scene_action,
        **({'workspace_project': workspace_project} if workspace_project is not None else {}),
    }
    async with Client(endpoint) as remote:
        result = await remote.call_tool("execute_api", {"params": payload}, raise_on_error=False)
    if result.is_error:
        receipt = result.structured_content
        # PipelineMCP marks business failures as MCP errors while retaining receipts.
        if isinstance(receipt, dict) and is_failure(receipt.get("status")):
            return receipt
        if result.content and isinstance(result.content[0], TextContent):
            raise ToolError(result.content[0].text)
        raise ToolError("Tool 'execute_api' returned an error")
    if result.data is not None:
        return result.data
    return result.structured_content


def execute_sync(*args, **kwargs) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(execute(*args, **kwargs))
    raise RuntimeError("execute_sync cannot run inside an active event loop; await client.execute instead")
