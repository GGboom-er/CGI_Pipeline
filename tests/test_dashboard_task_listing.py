"""Bulk audit lookup and nonblocking Dashboard refresh regressions."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from threading import Event

import httpx
import pytest

from cgi_pipeline.core import task_audit


ROOT = Path(__file__).resolve().parents[1]


def test_bulk_audit_lookup_preserves_newest_dated_then_flat_and_enumerates_once(tmp_path, monkeypatch):
    newest, older = tmp_path / '2026-10-14', tmp_path / '2026-10-13'
    newest.mkdir(); older.mkdir()
    for directory, names in [(tmp_path, ['same', 'flat']), (older, ['same', 'older']),
                             (newest, ['same', 'newest'])]:
        for name in names:
            (directory / f'{name}.json').write_text('{}')
    original_iterdir, original_isdir = Path.iterdir, Path.is_dir
    enumerations, statted = [], []

    def counted(path):
        enumerations.append(path)
        return original_iterdir(path)

    def checked(path):
        statted.append(path)
        return original_isdir(path)

    monkeypatch.setattr(Path, 'iterdir', counted)
    monkeypatch.setattr(Path, 'is_dir', checked)
    files = task_audit.find_audit_files(tmp_path)
    assert files == {'same': newest / 'same.json', 'newest': newest / 'newest.json',
                     'older': older / 'older.json', 'flat': tmp_path / 'flat.json'}
    assert enumerations.count(tmp_path) == 1
    assert enumerations.count(newest) == enumerations.count(older) == 1
    assert not any(path.suffix == '.json' for path in statted)
    assert task_audit.find_audit_file('same', tmp_path) == newest / 'same.json'
    assert task_audit.find_audit_file('flat', tmp_path) == tmp_path / 'flat.json'
    assert task_audit.find_audit_files(tmp_path / 'absent') == {}


def test_task_list_disk_reads_leave_compile_endpoint_runnable(tmp_path, monkeypatch):
    from cgi_pipeline.dashboard import app as dashboard
    (tmp_path / 'api-test.json').write_text('{}')
    monkeypatch.setattr(dashboard, 'AUDIT_DIR', tmp_path)
    entered, release = Event(), Event()
    read_order = []

    def blocking_read(path):
        entered.set()
        # Bounded escape for the buggy event-loop-blocking implementation.
        read_order.append(release.wait(5))
        return [{'task_id': 'api-test', 'status': 'SUCCESS'}]

    monkeypatch.setattr(dashboard, 'read_entries', blocking_read)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=dashboard.app), base_url='http://test') as client:
            listing = asyncio.create_task(client.get('/api/tasks'))
            assert await asyncio.to_thread(entered.wait, 6)
            try:
                compiled = await client.post('/api/workflows/compile', json={
                    'workflow_id': 'blender_to_maya_full_build', 'extra_params': {}})
                assert compiled.json()['status'] == 'SUCCESS'
            finally:
                release.set()
            result = await listing
            assert result.json()['tasks'][0]['task_id'] == 'api-test'

    asyncio.run(scenario())
    assert read_order == [True], 'Compilation must finish while the audit read is waiting'


def test_task_list_does_not_repeat_single_lookup_for_known_paths(tmp_path, monkeypatch):
    from cgi_pipeline.dashboard import app as dashboard
    from fastapi.testclient import TestClient
    (tmp_path / 'api-test.json').write_text(json.dumps({'task_id': 'api-test', 'status': 'SUCCESS'}))
    monkeypatch.setattr(dashboard, 'AUDIT_DIR', tmp_path)
    monkeypatch.setattr(dashboard, '_find_audit_file', lambda *a: (_ for _ in ()).throw(
        AssertionError('Bulk listing must not repeat single-task lookup')))
    result = TestClient(dashboard.app).get('/api/tasks').json()
    assert [task['task_id'] for task in result['tasks']] == ['api-test']


@pytest.mark.parametrize('blocked_operation', ['service_status', 'worker_ensure', 'worker_health'])
def test_status_io_leaves_compile_endpoint_runnable(monkeypatch, blocked_operation):
    from cgi_pipeline.dashboard import app as dashboard
    from cgi_pipeline.core import service_manager
    entered, release = Event(), Event()
    read_order = []

    def wait_then(value):
        entered.set()
        read_order.append(release.wait(5))
        return value

    if blocked_operation == 'service_status':
        monkeypatch.setattr(dashboard, 'get_service_status', lambda **kw: wait_then(
            {'redis': {}, 'worker_cgi': {}, 'dashboard': {}}))
        route = '/api/service_status'
    else:
        monkeypatch.setattr(dashboard, 'ensure_ready', lambda *a: wait_then((True, ''))
                            if blocked_operation == 'worker_ensure' else (True, ''))
        monkeypatch.setattr(service_manager, 'get_worker_health', lambda *a: wait_then(
            {'state': 'HEALTHY', 'pid_alive': True, 'pid': 1}) if blocked_operation == 'worker_health'
            else {'state': 'HEALTHY', 'pid_alive': True, 'pid': 1})
        route = '/api/worker_status'

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=dashboard.app), base_url='http://test') as client:
            status = asyncio.create_task(client.get(route))
            assert await asyncio.to_thread(entered.wait, 6)
            try:
                compiled = await client.post('/api/workflows/compile', json={
                    'workflow_id': 'blender_to_maya_full_build', 'extra_params': {}})
                assert compiled.json()['status'] == 'SUCCESS'
            finally:
                release.set()
            assert (await status).status_code == 200

    asyncio.run(scenario())
    assert read_order == [True], f'Compilation must finish while {blocked_operation} waits'


def test_task_refresh_only_runs_on_visible_panel_and_deduplicates_requests():
    node = shutil.which('node')
    assert node
    result = subprocess.run([node, str(ROOT / 'tests/dashboard_task_refresh_probe.js'),
                             str(ROOT / 'src/cgi_pipeline/dashboard/static/node_editor.html')],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS:' in result.stdout
