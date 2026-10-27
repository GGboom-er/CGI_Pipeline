"""Run the existing FastMCP HTTP app with authenticated, drained shutdown."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import fastmcp
import uvicorn

from cgi_pipeline.paths import REPOSITORY_ROOT
from cgi_pipeline.server.http_control import HttpControl
from cgi_pipeline.server.http_lifecycle import lifecycle


_LOOPBACK_NAMES = frozenset({'127.0.0.1', 'localhost', '::1'})


def _host_name(value: str) -> str:
    """'127.0.0.1:8000' -> '127.0.0.1', '[::1]:8000' -> '::1'."""
    if value.startswith('['):
        return value[1:].split(']', 1)[0]
    return value.rsplit(':', 1)[0] if value.count(':') == 1 else value


class LoopbackGuard:
    """Refuse requests a web page could forge via DNS rebinding (MCP Streamable HTTP: validate Origin).

    Host must name a loopback address; an Origin, when present, must be loopback too. Local MCP
    clients connect to http://127.0.0.1:<port>/mcp and send no browser Origin, so they pass.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            headers = {key.lower(): value.decode('latin-1') for key, value in scope.get('headers', [])}
            origin = headers.get(b'origin')
            allowed = _host_name(headers.get(b'host', '').lower()) in _LOOPBACK_NAMES and (
                origin is None or (urlsplit(origin).hostname or '') in _LOOPBACK_NAMES)
            if not allowed:
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                    return
                body = b'Forbidden: non-loopback Host or Origin'
                await send({'type': 'http.response.start', 'status': 403,
                            'headers': [(b'content-type', b'text/plain'), (b'content-length', str(len(body)).encode())]})
                await send({'type': 'http.response.body', 'body': body})
                return
        await self.app(scope, receive, send)


async def serve_http(mcp, *, host: str, port: int, metadata_path: Path | None = None):
    """Keep FastMCP's session/server lifespans and transport settings intact."""
    app = mcp.http_app(transport='streamable-http')
    config = uvicorn.Config(
        LoopbackGuard(app), host=host, port=port, lifespan='on', ws='websockets-sansio',
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
