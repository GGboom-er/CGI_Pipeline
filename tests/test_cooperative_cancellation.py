import asyncio
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from cgi_pipeline.core import tasks, progress, service_manager
from cgi_pipeline.core.task_audit import read_entries, task_state
from cgi_pipeline.dashboard import app as dashboard
from cgi_pipeline.server import internals


API = 'pipeline.pipeline.resolve_asset_files'


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / 'audit'
    root.mkdir()
    monkeypatch.setattr(tasks, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(dashboard, 'AUDIT_DIR', root)
    monkeypatch.setattr(internals, 'AUDIT_DIR', root)
    monkeypatch.setattr(tasks, '_update_progress', lambda *a: None)
    monkeypatch.setattr(tasks, 'publish_event', lambda *a: None)
    monkeypatch.setattr(progress, 'get_latest_state', lambda _: {'event_type': 'step.start'})
    fake = Mock()
    fake.AsyncResult.return_value.state = 'PENDING'
    monkeypatch.setattr(service_manager, 'get_celery_app', lambda: fake)
    client = TestClient(dashboard.app)
    payload = {'task_id': 'chain-cancel-test', 'source_path': '', 'project': 'default',
               'asset_name': 'fixture', 'run_dir': str(tmp_path / 'run'),
               'suppress_report': True,
               'api_chain': [{'api_id': API}, {'api_id': API}]}
    path = root / (payload['task_id'] + '.json')
    path.write_text(json.dumps({'status': 'SUBMITTED'}) + '\n')
    return payload, path, client


def cancel(client, task_id):
    response = client.post(f'/api/tasks/{task_id}/cancel')
    assert response.status_code == 200
    assert response.json()['requested']


def test_queued_cancel_is_confirmed_without_creating_worker(runtime, monkeypatch):
    payload, path, client = runtime
    worker = Mock(side_effect=AssertionError('Must not start DCC'))
    monkeypatch.setattr(tasks, '_create_worker', worker)
    cancel(client, payload['task_id'])
    result = tasks.execute_api_chain.run(payload)
    assert result['status'] == 'CANCELLED'
    worker.assert_not_called()
    assert internals._read_audit(payload['task_id'])['status'] == 'CANCELLED'
    assert client.get(f"/api/tasks/{payload['task_id']}").json()['terminal']


@pytest.mark.parametrize('cancel_after,expected_calls', [(1, 1), (2, 2)])
def test_request_stops_before_repair_or_retry_and_preserves_receipts(runtime, monkeypatch, cancel_after, expected_calls):
    payload, path, client = runtime
    payload['api_chain'] = [{'api_id': API, 'failure_policy': {
        'action': 'repair_then_retry', 'repair_step': {'api_id': API},
        'max_attempts': 1, 'on_exhausted': 'needs_attention'}}]
    calls = []

    class Worker:
        def run_api(self, item):
            calls.append(item)
            if len(calls) == cancel_after:
                cancel(client, payload['task_id'])
            status = 'AUDIT_FAILED' if len(calls) == 1 else 'SUCCESS'
            return {'status': status, 'detail': json.dumps({'api_id': API, 'status': status, 'output': {'call': len(calls)}})}

    monkeypatch.setattr(tasks, '_create_worker', lambda *a, **k: Worker())
    result = tasks.execute_api_chain.run(payload)
    assert result['status'] == 'CANCELLED'
    assert len(calls) == expected_calls
    assert result['interrupted_step']['initial_receipt']['status'] == 'AUDIT_FAILED'
    if cancel_after == 2:
        assert result['interrupted_step']['repair_receipt']['status'] == 'SUCCESS'
    assert task_state(read_entries(path))['status'] == 'CANCELLED'


@pytest.mark.parametrize('steps,expected_status', [(1, 'SUCCESS'), (2, 'CANCELLED')])
def test_inflight_call_finishes_then_next_call_is_suppressed(runtime, monkeypatch, steps, expected_status):
    payload, path, client = runtime
    payload['api_chain'] = payload['api_chain'][:steps]
    calls = []

    class Worker:
        def run_api(self, item):
            calls.append(item)
            cancel(client, payload['task_id'])
            return {'status': 'SUCCESS', 'detail': json.dumps({'api_id': API, 'status': 'SUCCESS', 'output': {'retained': True}})}

    monkeypatch.setattr(tasks, '_create_worker', lambda *a, **k: Worker())
    result = tasks.execute_api_chain.run(payload)
    assert result['status'] == expected_status
    assert len(calls) == 1
    assert len(result['chain_results']) == 1
    public_status = 'CHAIN_SUCCESS' if expected_status == 'SUCCESS' else expected_status
    assert internals._read_audit(payload['task_id'])['status'] == public_status
    assert client.get('/api/tasks').json()['tasks'][0]['status'] == public_status
    response = client.get(f"/api/tasks/{payload['task_id']}/poll")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert sum(event['terminal'] for event in events) == 1
    assert events[-1]['status'] == public_status


def test_completion_wins_late_request_and_step_failure_is_not_terminal():
    running = {'status': 'TASK_RUNNING', 'scope': 'task'}
    request = {'status': 'CANCEL_REQUESTED'}
    step = {'status': 'STEP_ERROR'}
    assert not task_state([running, step])['terminal']
    assert task_state([request, running, step])['status'] == 'CANCEL_REQUESTED'
    assert task_state([running, request, step])['status'] == 'CANCEL_REQUESTED'
    assert task_state([running, {'status': 'SUCCESS', 'scope': 'task'}, request])['status'] == 'SUCCESS'
    assert not task_state([running, {'status': 'CANCELLED', 'scope': 'segment'}])['terminal']
    assert not task_state([step, {'status': 'STEP_START'}])['terminal']
    assert task_state([{'status': 'SUCCESS'}, request])['status'] == 'SUCCESS'


def test_single_api_and_workflow_queued_cancel_share_confirmation(runtime, monkeypatch):
    payload, path, client = runtime
    for operation in (tasks.execute_api_operation, tasks.execute_workflow):
        path.write_text(json.dumps({'status': 'CANCEL_REQUESTED'}) + '\n')
        result = operation.run({**payload, 'api_id': API})
        assert result['status'] == 'CANCELLED'
        assert task_state(read_entries(path))['terminal']
