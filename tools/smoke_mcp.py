#!/usr/bin/env python3
"""Call the live MCP catalog and verify its public metadata contract."""

from __future__ import annotations

import argparse
import asyncio
import json

from fastmcp import Client


async def probe(endpoint: str) -> dict:
    async with Client(endpoint) as client:
        tools = await client.list_tools()
        result = await client.call_tool("list_apis", {})
        filtered = await client.call_tool(
            "list_apis", {"executor": "maya", "search": "skin.query_weights", "limit": 20}
        )
        help_result = await client.call_tool("api_help", {"api_id": "maya.scene.selection.get"})
    data = result.data
    filtered_data = filtered.data
    help_data = help_result.data
    apis = data["apis"]
    tool_schemas = {
        tool.name: json.dumps(tool.model_dump(by_alias=True), sort_keys=True)
        for tool in tools
    }
    required = {"api_id", "access", "modes", "summary"}
    checks = {
        "total": data["total"] >= 87,
        "compact_page": 0 < len(apis) <= 20 and bool(data.get("next_cursor")),
        "facets": bool(apis) and all(required <= set(row) for row in apis),
        "progressive_filter": any(
            row["api_id"] == "maya.rig.skin.query_weights"
            for row in filtered_data["apis"]
        ),
        "single_help": help_data.get("api_id") == "maya.scene.selection.get",
        "contract_version": (
            help_data.get("catalog_version") == data.get("catalog_version")
        ),
        "direct_execute_schema": (
            "expected_catalog_version" in tool_schemas.get("execute_api", "")
        ),
        "direct_batch_schema": (
            "expected_catalog_version" in tool_schemas.get("execute_batch", "")
        ),
        "public_surface": {tool.name for tool in tools} == {
            "list_apis", "api_help", "execute_api", "execute_batch",
            "get_task_result", "list_workflows", "pipeline_execute_workflow",
        },
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "endpoint": endpoint,
        "total": data["total"],
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/mcp")
    args = parser.parse_args()
    result = asyncio.run(probe(args.endpoint))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
