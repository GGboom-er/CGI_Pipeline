from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
from cgi_pipeline import cli
from cgi_pipeline.dashboard import app as dashboard
from cgi_pipeline.server import internals


def test_cli_and_dashboard_api_preserve_parameters_and_task_binding(monkeypatch):
    submit = Mock(return_value={'status': 'ERROR', 'error_code': 'TEST_REJECT'})
    monkeypatch.setattr(internals, '_submit_api_to_celery', submit)
    args = SimpleNamespace(api_id='pipeline.session.discover', project='ysj', asset='test',
                           source_path='', param=['dcc=maya'], business_task_id='PL251', workspace_id=None)
    assert cli.cmd_run_api(args)['error_code'] == 'TEST_REJECT'
    cli_payload = submit.call_args.args[1]
    client = TestClient(dashboard.app)
    result = client.post('/api/execute_api', json={
        'api_id': args.api_id, 'project': 'ysj', 'asset_name': 'test', 'source_path': '',
        'parameters': {'dcc': 'maya'}, 'business_task_id': 'PL251'}).json()
    assert result['dispatched'] is False
    assert submit.call_args.args[1] == cli_payload


def test_cli_and_dashboard_chain_share_submission(monkeypatch):
    submit = Mock(return_value={'status': 'SUBMIT_FAILED', 'error': 'offline'})
    monkeypatch.setattr(internals, '_submit_chain', submit)
    args = SimpleNamespace(steps='one,two', project='ysj', asset='test', source_path='',
                           param=['count=2'], business_task_id='PL251', workspace_id=None)
    assert cli.cmd_run_chain(args)['status'] == 'SUBMIT_FAILED'
    payload = submit.call_args.args[0]
    assert payload['api_chain'] == [{'api_id': 'one', 'parameters': {}},
                                    {'api_id': 'two', 'parameters': {'count': 2}}]
    client = TestClient(dashboard.app)
    result = client.post('/api/execute_graph', json=payload).json()
    assert not result['dispatched']
    assert submit.call_args.args[0] == payload


def test_cli_wait_recovers_original_task_without_resubmit(monkeypatch):
    get = Mock(side_effect=[{'status': 'PROGRESS'}, {'status': 'SUCCESS', 'task_id': 'api-test'}])
    monkeypatch.setattr(internals, 'get_task_result', get)
    monkeypatch.setattr(cli.time, 'sleep', lambda _: None)
    assert cli._print_submitted_result({'status': 'SUBMITTED', 'task_id': 'api-test'})['status'] == 'SUCCESS'
    assert [call.args for call in get.call_args_list] == [('api-test',), ('api-test',)]


def test_dashboard_invalid_api_rejected_before_worker(monkeypatch):
    ensure = Mock(side_effect=AssertionError('must not start worker'))
    monkeypatch.setattr(internals, '_ensure_worker', ensure)
    result = TestClient(dashboard.app).post('/api/execute_api', json={'api_id': 'nonexistent'}).json()
    assert result['error_code'] == 'API_NOT_FOUND'
    assert not result['dispatched']
    ensure.assert_not_called()
