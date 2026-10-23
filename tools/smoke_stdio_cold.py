"""Isolated real Redis/Worker STDIO smoke; never stops the production runtime.

The fixture changes only runtime/log locations and Redis probe destination.
Actual MCP entrypoint, service launch, Celery and read-only API remain real.
Temporary evidence is retained for inspection.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

import psutil
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from cgi_pipeline.core import service_manager as sm


async def probe():
    root = Path(tempfile.mkdtemp(prefix='cgi-stdio-cold-'))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config = root / 'redis.conf'
    config.write_text('intentionally-invalid-setting yes\n', encoding='ascii')
    before = sm.is_worker_alive('cgi')
    bootstrap = '''
import os, runpy
from pathlib import Path
from cgi_pipeline.core import service_manager as sm
sm.RUNTIME_DIR = Path(os.environ['SMOKE_ROOT']) / 'runtime'
sm.LOGS_DIR = Path(os.environ['SMOKE_ROOT']) / 'logs'
original_probe = sm.is_redis_alive
sm.is_redis_alive = lambda: original_probe(port=int(os.environ['SMOKE_PORT']))
runpy.run_module('cgi_pipeline.server.server', run_name='__main__')
'''
    def connect():
        return Client(StdioTransport(
            command=sys.executable, args=['-s', '-c', bootstrap], cwd=str(root),
            keep_alive=False, log_file=root / 'server.stderr.log', env={
                'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                'SMOKE_ROOT': str(root), 'SMOKE_PORT': str(port),
                'REDIS_CONFIG_PATH': str(config),
                'REDIS_URL': f'redis://127.0.0.1:{port}/0',
                'REDIS_RESULT_URL': f'redis://127.0.0.1:{port}/1',
            }), timeout=90)
    async def discover(client):
        help_data = (await client.call_tool('api_help', {'api_id': 'pipeline.session.discover'})).data
        return (await client.call_tool('execute_api', {'params': {
            'api_id': 'pipeline.session.discover', 'params': {'dcc': 'ue'},
            'expected_catalog_version': help_data['catalog_version'],
        }})).data
    def executed(result):
        return result['status'] == 'SUCCESS' or (
            result['status'] == 'NEEDS_ATTENTION'
            and result.get('receipt', {}).get('error_code') == 'NO_UNIQUE_SESSION')
    worker = None
    results = []
    try:
        async with connect() as first:
            assert await first.list_tools()
            failed = await discover(first)
            assert failed['status'] == 'SUBMIT_FAILED', failed
            assert not (root / 'runtime' / 'worker_cgi.pid').exists()
            config.write_text(f'bind 127.0.0.1\nport {port}\nsave ""\nappendonly no\ndir {root.as_posix()}\n', encoding='ascii')
            async with connect() as second:
                results = await asyncio.gather(discover(first), discover(second))
                assert all(executed(item) for item in results), results
                worker = psutil.Process(int((root / 'runtime' / 'worker_cgi.pid').read_text()))
                identity = (worker.pid, worker.create_time())
            assert worker.is_running()
            survivor = await discover(first)
            assert executed(survivor), survivor
            results.append(survivor)
        assert worker.is_running() and worker.create_time() == identity[1]
        async with connect() as restarted:
            again = await discover(restarted)
            assert executed(again), again
            assert int((root / 'runtime' / 'worker_cgi.pid').read_text()) == identity[0]
            results.append(again)
        assert sm.is_worker_alive('cgi') == before
        report = {'status': 'PASS', 'evidence_dir': str(root), 'isolated_port': port,
                  'worker': identity, 'production_worker': before,
                  'checks': ['catalog_without_runtime', 'redis_failure_returned',
                             'same_client_retry', 'concurrent_cold_start',
                             'survivor_after_peer_exit', 'worker_survives_all_exits',
                             'new_client_reuses_worker'],
                  'failed_status': failed['status'],
                  'receipts': [{'task_id': item['task_id'], 'status': item['status']} for item in results]}
        (root / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
    finally:
        # Only processes identified by this unique fixture's paths are eligible.
        import redis
        try:
            redis.Redis(host='127.0.0.1', port=port, socket_timeout=2).shutdown(nosave=True)
        except redis.ConnectionError:
            pass
        owned = []
        for proc in psutil.process_iter(['pid', 'cmdline']):
            command = proc.info['cmdline'] or []
            if str(config) in command or f'--pidfile={root / "runtime" / "worker_cgi.pid"}' in command:
                owned.append(proc)
        for proc in owned:
            try:
                if 'redis' not in proc.name().lower():
                    assert sm._kill_process_tree(proc.pid)
            except psutil.NoSuchProcess:
                pass
        assert sm.is_worker_alive('cgi') == before


if __name__ == '__main__':
    print(json.dumps(asyncio.run(probe()), indent=2))
