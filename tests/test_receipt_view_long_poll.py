"""Bounded default receipts and server-side waiting for task recovery (PL304 B1)."""

import asyncio
import json

from cgi_pipeline.server import receipt_view, tools_operations, tools_readonly
from cgi_pipeline.server.models import ExecuteApiInput, TaskQueryInput
from cgi_pipeline.server.receipt_view import SUMMARY_LIMIT_BYTES, project_result


class CapturedTools:
    def tool(self, **metadata):
        def register(function):
            setattr(self, metadata['name'], function)
            return function
        return register


def _size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def _large_api_result():
    return {
        'task_id': 'api-big', 'api_id': 'maya.scene.hierarchy.get', 'status': 'SUCCESS', 'terminal': True,
        'audit_path': 'Y:/audit/api-big.json', 'workspace_id': 'PL1', 'report_path': 'Y:/r/REPORT.md',
        'entries': [{'status': 'STARTED', 'detail': 'x' * 700}] * 3,
        'receipt': {
            'api_id': 'maya.scene.hierarchy.get', 'api_version': '1', 'status': 'SUCCESS',
            'input': {'params': {'root': '|grp'}}, 'elapsed_sec': 1.2, 'error_code': '', 'error': '',
            'artifacts': [f'Y:/out/{index}.png' for index in range(30)],
            'output': {'count': 4000, 'nodes': [f'|grp|node_{index}' for index in range(4000)],
                       'output_path': 'Y:/out/hierarchy.json'},
        },
    }


def test_small_result_is_returned_unchanged():
    small = {'task_id': 'api-1', 'status': 'SUCCESS', 'receipt': {'status': 'SUCCESS', 'output': {'value': 42}}}
    assert project_result(small) is small


def test_large_api_result_is_summarized_and_detail_keeps_everything():
    full = _large_api_result()
    view = project_result(full)
    assert _size(view) <= SUMMARY_LIMIT_BYTES
    assert view['status'] == 'SUCCESS' and view['task_id'] == 'api-big'
    assert view['receipt_ref']['full_bytes'] == _size(full)
    assert view['receipt']['artifacts'][:2] == full['receipt']['artifacts'][:2]
    assert view['receipt']['artifacts_total'] == 30
    output = view['receipt']['output']
    assert output['count'] == 4000 and output['output_path'] == 'Y:/out/hierarchy.json'
    assert output['nodes']['omitted_bytes'] > 0 and output['nodes']['len'] == 4000
    assert 'entries' not in view and 'input' not in view['receipt']
    assert project_result(full, detail=True) is full


def test_large_workflow_result_stays_within_budget():
    steps = [{'step': index, 'api_id': f'maya.step.{index}', 'status': 'SUCCESS'} for index in range(80)]
    steps[5]['status'] = 'ERROR'
    full = {
        'task_id': 'wf-1', 'workflow_id': 'proxy_rig', 'status': 'WORKFLOW_ERROR', 'steps': steps,
        'step_checklist': '\n'.join(f'{index}. step' for index in range(80)) * 4,
        'step_receipts': [{'api_id': step['api_id'], 'status': step['status'], 'entry_index': step['step'],
                           'receipt': {'status': step['status'], 'output': {'blob': 'y' * 2000},
                                       'error': 'broken' if step['status'] == 'ERROR' else ''}}
                          for step in steps],
        'outputs': {f'key_{index}': 'z' * 300 for index in range(40)},
    }
    view = project_result(full)
    assert _size(view) <= SUMMARY_LIMIT_BYTES
    assert view['workflow_id'] == 'proxy_rig' and view['status'] == 'WORKFLOW_ERROR'
    assert view['receipt_ref']['read'].startswith('get_task_result')


def test_wait_is_capped_below_client_timeout(monkeypatch):
    monkeypatch.setenv('CGI_MCP_MAX_WAIT_SEC', '1')
    monkeypatch.setattr(tools_operations, 'get_task_result',
                        lambda task_id: {'task_id': task_id, 'status': 'STARTED', 'step': 2, 'workspace_id': 'PL1'})
    result = asyncio.run(tools_operations._wait_for_task('api-slow', 1800, 0.05))
    assert result['status'] == 'PROGRESS' and result['last_status'] == 'STARTED'
    assert result['step'] == 2 and result['workspace_id'] == 'PL1'
    assert '1s' in result['message']
    assert receipt_view.max_wait_sec() == 1
    assert ExecuteApiInput(api_id='x').wait_timeout_sec is None


def test_get_task_result_waits_server_side_until_terminal(monkeypatch):
    states = iter(['STARTED', 'PROGRESS', 'PROGRESS', 'SUCCESS'])
    reads = []

    def read(task_id):
        reads.append(task_id)
        return {'task_id': task_id, 'status': next(states)}

    monkeypatch.setattr(tools_readonly, 'get_task_result', read)
    monkeypatch.setattr(tools_operations, 'get_task_result', read)
    tools = CapturedTools()
    tools_readonly.register_readonly_tools(tools)
    result = asyncio.run(tools.get_task_result(TaskQueryInput(task_id='api-run', wait_sec=5)))
    assert result['status'] == 'SUCCESS' and len(reads) == 4


def test_get_task_result_returns_at_once_for_snapshot_and_unknown_ids(monkeypatch):
    reads = []

    def read(task_id):
        reads.append(task_id)
        return {'task_id': task_id, 'status': 'NOT_FOUND' if task_id == 'missing' else 'STARTED'}

    monkeypatch.setattr(tools_readonly, 'get_task_result', read)
    monkeypatch.setattr(tools_operations, 'get_task_result', read)
    tools = CapturedTools()
    tools_readonly.register_readonly_tools(tools)
    assert asyncio.run(tools.get_task_result(TaskQueryInput(task_id='missing')))['status'] == 'NOT_FOUND'
    assert asyncio.run(tools.get_task_result(TaskQueryInput(task_id='api-run', wait_sec=0)))['status'] == 'STARTED'
    assert reads == ['missing', 'api-run']
