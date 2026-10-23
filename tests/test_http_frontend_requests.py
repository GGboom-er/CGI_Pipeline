"""Request ownership across cancellation, synchronous IO and durable audit."""

import asyncio
import threading

import pytest
from fastmcp.tools.base import ToolResult
from types import SimpleNamespace

from cgi_pipeline.server import mcp_audit, tools_readonly
from cgi_pipeline.server.http_lifecycle import HttpLifecycle
from cgi_pipeline.server.models import TaskQueryInput


class CapturedTools:
    def tool(self, **metadata):
        def register(function):
            setattr(self, metadata['name'], function)
            return function
        return register


def test_slow_recovery_does_not_block_the_event_loop(monkeypatch):
    life = HttpLifecycle()
    monkeypatch.setattr(tools_readonly, 'lifecycle', life)
    entered, release = threading.Event(), threading.Event()

    def query(task_id):
        entered.set()
        assert release.wait(2), 'the event loop was blocked by recovery IO'
        return {'task_id': task_id, 'status': 'SUCCESS'}

    monkeypatch.setattr(tools_readonly, 'get_task_result', query)
    tools = CapturedTools()
    tools_readonly.register_readonly_tools(tools)

    async def run():
        task = asyncio.create_task(tools.get_task_result(TaskQueryInput(task_id='api-test')))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            assert life.status()['active_count'] > 0
            # This must execute while the query is still blocked in its thread.
            release.set()
            assert (await task)['status'] == 'SUCCESS'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert life.status()['active_count'] == 0

    asyncio.run(run())


def test_cancelled_client_keeps_work_and_audit_owned(monkeypatch, tmp_path):
    life = HttpLifecycle()
    monkeypatch.setattr(mcp_audit, 'lifecycle', life)
    entered, release = threading.Event(), threading.Event()
    audit_entered, audit_release = threading.Event(), threading.Event()
    middleware = mcp_audit.McpCallAuditMiddleware(tmp_path / 'calls.jsonl')
    original_write = middleware._write_safely

    def write(record):
        audit_entered.set()
        assert audit_release.wait(2)
        original_write(record)

    monkeypatch.setattr(middleware, '_write_safely', write)

    def work():
        entered.set()
        assert release.wait(2)

    async def next_call(_context):
        await life.run_sync(work)
        return ToolResult(structured_content={'status': 'SUCCESS'})

    async def run():
        owned_tasks = []
        original_create = asyncio.create_task

        def capture(coroutine, **kwargs):
            task = original_create(coroutine, **kwargs)
            if '_owned_call' in coroutine.__qualname__:
                owned_tasks.append(task)
            return task

        monkeypatch.setattr(asyncio, 'create_task', capture)
        context = SimpleNamespace(message=SimpleNamespace(name='execute_api'), fastmcp_context=None)
        request = asyncio.get_running_loop().create_task(middleware.on_call_tool(context, next_call))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            state = life.status()
            life.drain(state['lease_epoch'], 10)
            assert life.status()['active_count'] > 0
            release.set()
            assert await asyncio.to_thread(audit_entered.wait, 1)
            assert life.status()['active_count'] > 0
            audit_release.set()
            await asyncio.wait_for(owned_tasks[0], 2)
            assert life.status()['active_count'] == 0
            assert 'SUCCESS' in (tmp_path / 'calls.jsonl').read_text(encoding='utf-8')
        finally:
            release.set()
            audit_release.set()
            await asyncio.gather(*owned_tasks, return_exceptions=True)

    asyncio.run(run())


def test_drain_refuses_before_tool_or_audit_side_effects(monkeypatch, tmp_path):
    life = HttpLifecycle()
    monkeypatch.setattr(mcp_audit, 'lifecycle', life)
    life.drain(life.status()['lease_epoch'], 10)
    middleware = mcp_audit.McpCallAuditMiddleware(tmp_path / 'calls.jsonl')

    async def forbidden(_context):
        raise AssertionError('drained request reached business dispatch')

    context = SimpleNamespace(message=SimpleNamespace(name='execute_api'), fastmcp_context=None)
    result = asyncio.run(middleware.on_call_tool(context, forbidden))
    assert result.structured_content['error_code'] == 'HTTP_DRAINING'
    assert result.structured_content['submitted'] is False
    assert not (tmp_path / 'calls.jsonl').exists()
