import asyncio
import json
from unittest.mock import Mock

import pytest
from cgi_pipeline.dashboard import app as dashboard
from cgi_pipeline.server import internals
from cgi_pipeline.core import progress, service_manager


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    fake = Mock()
    fake.AsyncResult.return_value.state = 'PENDING'
    monkeypatch.setattr(dashboard, 'AUDIT_DIR', tmp_path)
    monkeypatch.setattr(internals, 'AUDIT_DIR', tmp_path)
    monkeypatch.setattr(dashboard, 'get_celery_app', lambda: fake)
    monkeypatch.setattr(service_manager, 'get_celery_app', lambda: fake)
    monkeypatch.setattr(progress, 'get_latest_state', lambda _: {'event_type': 'step.start'})
    return tmp_path, fake


@pytest.mark.parametrize('dated', [False, True])
def test_cancel_request_detail_list_and_mcp_share_record(runtime, dated):
    root, fake = runtime
    folder = root / '2026-09-22' if dated else root
    folder.mkdir(exist_ok=True)
    path = folder / 'api-test.json'
    path.write_text(json.dumps({'task_id': 'api-test', 'status': 'STARTED'}) + '\n')
    result = asyncio.run(dashboard.cancel_task('api-test'))
    assert result['requested'] and not result['cancelled']
    fake.control.revoke.assert_not_called()
    assert internals._read_audit('api-test')['status'] == 'CANCEL_REQUESTED'
    assert asyncio.run(dashboard.get_task_detail('api-test'))['entries'][-1]['status'] == 'CANCEL_REQUESTED'
    assert asyncio.run(dashboard.get_tasks())['tasks'][0]['status'] == 'CANCEL_REQUESTED'
    asyncio.run(dashboard.cancel_task('api-test'))
    assert fake.control.revoke.call_count == 0
    with path.open('a') as f:
        f.write(json.dumps({'status': 'SUCCESS'}) + '\n')
    assert internals._read_audit('api-test')['status'] == 'SUCCESS'
    assert not asyncio.run(dashboard.cancel_task('api-test'))['requested']


@pytest.mark.parametrize('task_id', ['missing', '../escape', ''])
def test_invalid_or_unknown_task_does_not_revoke(runtime, task_id):
    _, fake = runtime
    assert not asyncio.run(dashboard.cancel_task(task_id))['requested']
    fake.control.revoke.assert_not_called()


def test_queue_offline_does_not_prevent_durable_request(runtime):
    root, fake = runtime
    path = root / 'api-test.json'
    original = json.dumps({'status': 'STARTED'}) + '\n'
    path.write_text(original)
    fake.control.revoke.side_effect = RuntimeError('offline')
    result = asyncio.run(dashboard.cancel_task('api-test'))
    assert result['requested'] and not result['cancelled']
    assert 'CANCEL_REQUESTED' in path.read_text(encoding='utf-8')


def test_http_routes_use_same_record_without_starting_services(runtime):
    from fastapi.testclient import TestClient
    root, _ = runtime
    folder = root / '2026-09-22'
    folder.mkdir()
    (folder / 'api-test.json').write_text(json.dumps({'status': 'STARTED'}) + '\n')
    client = TestClient(dashboard.app)
    assert client.post('/api/tasks/api-test/cancel').json()['status'] == 'CANCEL_REQUESTED'
    assert client.get('/api/tasks/api-test').json()['entries'][-1]['status'] == 'CANCEL_REQUESTED'
    assert client.get('/api/tasks').json()['total'] == 1
