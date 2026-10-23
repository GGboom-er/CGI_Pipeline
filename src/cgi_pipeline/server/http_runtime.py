"""Run the existing FastMCP HTTP app with authenticated, drained shutdown."""

from __future__ import annotations

import asyncio
from pathlib import Path

import fastmcp
import uvicorn

from cgi_pipeline.paths import REPOSITORY_ROOT
from cgi_pipeline.server.http_control import HttpControl
from cgi_pipeline.server.http_lifecycle import lifecycle


async def serve_http(mcp, *, host: str, port: int, metadata_path: Path | None = None):
    """Keep FastMCP's session/server lifespans and transport settings intact."""
    app = mcp.http_app(transport='streamable-http')
    config = uvicorn.Config(
        app, host=host, port=port, lifespan='on', ws='websockets-sansio',
        log_level=fastmcp.settings.log_level.lower(), timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)
    control = HttpControl(
        lifecycle,
        metadata_path or REPOSITORY_ROOT / 'runtime' / f'mcp_http_{port}.control.json',
        lambda: setattr(server, 'should_exit', True),
    )
    app.routes.extend(control.routes())
    control.write_metadata()
    try:
        async with mcp._lifespan_manager():
            await server.serve()
    finally:
        control.close()


def run_http(mcp, *, host: str, port: int):
    asyncio.run(serve_http(mcp, host=host, port=port))
