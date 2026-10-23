"""Read-only live check of two STDIO clients sharing the CGI runtime."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from cgi_pipeline.core.service_manager import is_worker_alive


async def probe() -> dict:
    with tempfile.TemporaryDirectory(prefix='cgi-stdio-client-') as cwd:
        def connect():
            return Client(StdioTransport(
                command=sys.executable,
                args=['-s', '-m', 'cgi_pipeline.server.server'],
                cwd=cwd,
                keep_alive=False,
                env={'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'},
            ), timeout=60)

        receipts = []
        async def discover(client):
            help_result = (await client.call_tool('api_help', {'api_id': 'pipeline.session.discover'})).data
            result = (await client.call_tool('execute_api', {'params': {
                'api_id': 'pipeline.session.discover', 'params': {'dcc': 'ue'},
                'expected_catalog_version': help_result['catalog_version'],
            }})).data
            assert result['status'] == 'SUCCESS', result
            receipts.append({'task_id': result['task_id'], 'status': result['status'], 'audit_path': result.get('audit_path')})

        before = is_worker_alive('cgi')
        async with connect() as survivor:
            async with connect() as other:
                left, right = await asyncio.gather(survivor.list_tools(), other.list_tools())
                assert {t.name for t in left} == {t.name for t in right}
                assert {'list_apis', 'api_help', 'execute_api'} <= {t.name for t in left}
                await asyncio.gather(discover(survivor), discover(other))
                during = is_worker_alive('cgi')
            await discover(survivor)
            after_one_exit = is_worker_alive('cgi')
        after_both_exit = is_worker_alive('cgi')
        assert during[0] and during == after_one_exit == after_both_exit
        if before[0]:
            assert before == during
        return {'status': 'PASS', 'checks': {
            'arbitrary_client_cwd': True, 'same_tools': True,
            'both_clients_execute': True, 'survivor_executes_after_peer_exit': True,
            'worker_survives_both_exits': True,
        }, 'worker_before': before, 'worker_after': after_both_exit, 'receipts': receipts}


if __name__ == '__main__':
    result = asyncio.run(probe())
    print(json.dumps(result, ensure_ascii=False, indent=2))
