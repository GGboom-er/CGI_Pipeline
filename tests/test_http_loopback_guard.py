"""The HTTP front refuses requests a web page could forge via DNS rebinding."""
import asyncio

import pytest

from cgi_pipeline.server.http_runtime import LoopbackGuard


def _call(headers, kind="http"):
    reached, sent = [], []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    scope = {"type": kind, "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    asyncio.run(LoopbackGuard(app)(scope, receive, send))
    return reached, sent


@pytest.mark.parametrize("headers", [
    {"host": "127.0.0.1:8000"},
    {"host": "localhost:8000"},
    {"host": "[::1]:8000"},
    {"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:8000"},
    {"Host": "LOCALHOST:8000"},
])
def test_local_clients_pass(headers):
    reached, sent = _call({k.lower(): v for k, v in headers.items()})
    assert reached == ["http"] and sent == []


@pytest.mark.parametrize("headers", [
    {"host": "attacker.example:8000"},           # DNS rebinding keeps the attacker's Host
    {"host": "127.0.0.1:8000", "origin": "http://attacker.example"},
    {"host": "192.168.1.20:8000"},
    {},                                          # no Host at all
])
def test_browser_reachable_requests_are_refused(headers):
    reached, sent = _call(headers)
    assert reached == []
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 403


def test_lifespan_passes_through_untouched():
    reached, sent = _call({}, kind="lifespan")
    assert reached == ["lifespan"] and sent == []
