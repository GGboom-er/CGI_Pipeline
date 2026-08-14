"""单 CGI 队列与 workflow 不嵌套投递的契约测试。"""

import ast
import inspect
from unittest.mock import patch

from cgi_pipeline.server import internals
from cgi_pipeline.core import tasks
from cgi_pipeline.core.service_manager import _normalize_dcc, _queue_for_dcc, _worker_hostname


class _FakeCelery:
    def __init__(self):
        self.calls = []

    def send_task(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()


def test_all_dcc_aliases_use_one_queue_and_worker():
    for dcc in ('maya', 'blender', 'ue', 'pipeline', 'workflow', 'cgi'):
        assert _normalize_dcc(dcc) == 'cgi'
        assert _queue_for_dcc(dcc) == 'cgi_queue'
    assert _worker_hostname('maya') == 'cgi@%h'


def test_workflow_submit_ensures_only_canonical_worker():
    celery = _FakeCelery()
    ensure_calls = []
    with patch.object(internals, '_ensure_worker', side_effect=lambda dcc: ensure_calls.append(dcc) or (True, 'ok')), \
         patch('cgi_pipeline.core.workflow_engine.load_workflow', return_value={'workflow_id': 'wf', 'steps': []}), \
         patch('cgi_pipeline.core.service_manager.get_celery_app', return_value=celery), \
         patch.object(internals, '_append_submission_audit'):
        result = internals._submit_workflow({'workflow_id': 'wf'})

    assert result['status'] == 'SUBMITTED'
    assert ensure_calls == ['cgi']
    assert celery.calls[0][1]['queue'] == 'cgi_queue'


def test_workflow_has_no_nested_celery_dispatch():
    source = inspect.getsource(tasks.execute_workflow.run)
    tree = ast.parse(source)
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert 'apply_async' not in calls
    assert 'apply' in calls
