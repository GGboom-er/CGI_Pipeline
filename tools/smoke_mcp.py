#!/usr/bin/env python3
"""Call the live MCP catalog and verify its public metadata contract."""

from __future__ import annotations

import argparse
import asyncio
import json

from fastmcp import Client


async def probe(endpoint: str) -> dict:
    async with Client(endpoint) as client:
        result = await client.call_tool("list_apis", {})
    data = result.data
    apis = data["apis"]
    required = {
        "api_id", "executor", "capability", "operation", "stages", "targets",
        "access", "effects", "execution_modes", "surfaces", "modes",
    }
    checks = {
        "total": data["total"] >= 44,
        "facets": bool(apis) and all(required <= set(row) for row in apis),
        "skin_capability": any(
            row["api_id"] == "maya.rig.skin.query_weights" for row in apis
        ),
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

