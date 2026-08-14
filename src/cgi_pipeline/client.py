"""Lazy network client for CGI's MCP service."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping


DEFAULT_ENDPOINT = "http://127.0.0.1:8000/mcp"


async def execute(api_id: str, params: Mapping[str, Any] | None = None, *,
                  endpoint: str = DEFAULT_ENDPOINT, project: str = "default",
                  asset_name: str = "untitled", source_path: str = "",
                  execution_mode: str = "background", foreground_port: int | None = None,
                  wait: bool = True) -> Any:
    from fastmcp import Client

    payload = {
        "api_id": api_id, "params": dict(params or {}), "project": project,
        "asset_name": asset_name, "source_path": source_path,
        "execution_mode": execution_mode, "foreground_port": foreground_port,
        "wait": wait,
    }
    async with Client(endpoint) as remote:
        result = await remote.call_tool("execute_api", {"params": payload})
    return getattr(result, "data", result)


def execute_sync(*args, **kwargs) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(execute(*args, **kwargs))
    raise RuntimeError("execute_sync cannot run inside an active event loop; await client.execute instead")
