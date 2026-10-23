import json
import sys
import threading
from types import SimpleNamespace

import pytest

from cgi_pipeline.core.dcc_factory import WarmWorkerProxy
from cgi_pipeline.contracts import ApiContractError, validate_batch_result
from cgi_pipeline.hosts.maya.worker import MayaWorker, MayaCommandPortWorker
from cgi_pipeline.hosts.blender.worker import BlenderWorker
from cgi_pipeline.hosts import file_ipc
from cgi_pipeline.hosts.file_ipc import FileIpcTransport
from cgi_pipeline.server import foreground_client


def ipc_worker(tmp_path, monkeypatch, worker_type):
    monkeypatch.setattr(sys.modules[worker_type.__module__], 'PROJECT_ROOT', tmp_path)
    return worker_type()


def envelope(atomic=True):
    results = [{'status': 'SUCCESS'}, {'status': 'ERROR'}]
    if not atomic:
        results.append({'status': 'SUCCESS'})
    return {'status': 'ERROR', 'results': results,
            'completed': len(results), 'requested': 3, 'atomic': atomic, 'rolled_back': atomic,
            'elapsed_sec': 0.125}


@pytest.mark.parametrize('worker_type', [MayaWorker, BlenderWorker])
@pytest.mark.parametrize('encoded', [True, False])
@pytest.mark.parametrize('atomic', [True, False])
def test_ipc_batch_preserves_failed_envelope(tmp_path, monkeypatch, worker_type, encoded, atomic):
    worker = ipc_worker(tmp_path, monkeypatch, worker_type)
    expected = envelope(atomic)
    response = {'task_id': 'test', 'status': 'ERROR',
                'detail': json.dumps(expected) if encoded else expected}
    (worker.result_dir / 'test.json').write_text(json.dumps(response))
    payloads = [{'task_id': 'test', 'api_id': 'test'}] * 3
    assert worker.run_batch(payloads, atomic=atomic) == expected
    command = json.loads(worker.cmd_file.read_text())
    assert command['atomic'] is atomic
    assert command['batch'] == payloads


@pytest.mark.parametrize('worker_type', [MayaWorker, BlenderWorker])
@pytest.mark.parametrize('response', [
    {'status': 'ERROR', 'detail': 'adapter crashed'},
    {'status': 'SUCCESS', 'detail': {'status': 'SUCCESS', 'results': None}},
])
def test_ipc_batch_rejects_transport_errors(tmp_path, monkeypatch, worker_type, response):
    worker = ipc_worker(tmp_path, monkeypatch, worker_type)
    (worker.result_dir / 'test.json').write_text(json.dumps({'task_id': 'test', **response}))
    with pytest.raises((RuntimeError, ValueError)):
        worker.run_batch([{'task_id': 'test'}])


@pytest.mark.parametrize('worker_type', [MayaWorker, BlenderWorker, MayaCommandPortWorker])
def test_empty_batch_returns_error_without_transport(worker_type):
    result = worker_type.__new__(worker_type).run_batch([], atomic=False)
    assert result['status'] == 'ERROR'
    assert result['error_code'] == 'BATCH_EMPTY'
    assert result['results'] == []
    assert result['atomic'] is False


@pytest.mark.parametrize('atomic', [True, False])
def test_warm_proxy_forwards_batch_once(atomic):
    calls = []
    expected = envelope(atomic)
    def run_batch(payloads, *, atomic):
        calls.append((payloads, atomic))
        return expected
    proxy = WarmWorkerProxy.__new__(WarmWorkerProxy)
    proxy.real_worker = SimpleNamespace(run_batch=run_batch)
    payloads = [{'api_id': 'test'}]
    assert proxy.run_batch(payloads, atomic=atomic) is expected
    assert calls == [(payloads, atomic)]


def test_rpyc_business_failure_keeps_connection_and_complete_envelope():
    expected = envelope(False)
    calls = []
    def execute_main(fn, payload):
        calls.append(json.loads(payload))
        return json.dumps(expected)
    conn = SimpleNamespace(execute=lambda code: None, namespace={'_cgi_run_batch_json': object(), '_cgi_run_api_json': object()},
                           modules={'maya.utils': SimpleNamespace(executeInMainThreadWithResult=execute_main)})
    worker = MayaCommandPortWorker.__new__(MayaCommandPortWorker)
    worker._conn = conn
    worker._consecutive_failures = 1
    worker._ensure_connection = lambda: conn
    payloads = [{'api_id': 'test'}] * 3
    assert worker.run_batch(payloads, atomic=False) == expected
    assert calls == [{'batch': payloads, 'atomic': False}]
    assert worker._conn is conn
    assert worker._consecutive_failures == 0


@pytest.mark.parametrize('response', [None, {'status': 'SUCCESS', 'results': 'invalid'}])
def test_rpyc_rejects_malformed_response_and_invalidates_connection(response):
    calls = []
    def execute_main(fn, payload):
        calls.append(payload)
        return json.dumps(response)
    conn = SimpleNamespace(execute=lambda code: None, namespace={'_cgi_run_batch_json': object(), '_cgi_run_api_json': object()},
                           modules={'maya.utils': SimpleNamespace(executeInMainThreadWithResult=execute_main)})
    worker = MayaCommandPortWorker.__new__(MayaCommandPortWorker)
    worker._conn = conn
    worker._ensure_connection = lambda: conn
    with pytest.raises(RuntimeError, match='invalid batch response'):
        worker.run_batch([{'api_id': 'test'}])
    assert len(calls) == 1
    assert worker._conn is None


def test_warm_proxy_does_not_retry_type_error_after_execution():
    calls = []
    def run_batch(payloads, *, atomic):
        calls.append(payloads)
        raise TypeError('failure after execution started')
    proxy = WarmWorkerProxy.__new__(WarmWorkerProxy)
    proxy.real_worker = SimpleNamespace(run_batch=run_batch)
    with pytest.raises(TypeError, match='after execution started'):
        proxy.run_batch([{'api_id': 'test'}])
    assert len(calls) == 1


def test_foreground_returns_and_audits_business_error(monkeypatch):
    expected = envelope()
    calls, audits = [], []
    def run_batch(payloads, *, atomic):
        calls.append((payloads, atomic))
        return expected
    worker = SimpleNamespace(start=lambda: None, run_batch=run_batch)
    monkeypatch.setattr(foreground_client, '_get_worker', lambda port: (worker, threading.RLock()))
    monkeypatch.setattr(foreground_client, '_write_audit', lambda *args: audits.append(args))
    result = foreground_client.submit_foreground_batch_task({
        'task_id': 'test', 'api_context': {'foreground_port': 7123}, 'atomic': True,
        'api_chain': [{'api_id': 'test'}] * 3})
    assert result['status'] == 'ERROR'
    assert all(result['detail'][key] == value for key, value in expected.items())
    assert audits[-1][1] == 'ERROR'
    assert json.loads(audits[-1][3]) == result['detail']
    assert len(calls) == 1
    assert calls[0][1] is True


@pytest.mark.parametrize('updates', [
    {'status': 'SUCCESS', 'results': [{'status': 'SUCCESS'}], 'completed': 1},
    {'results': [{'status': 'SUCCESS'}], 'completed': 1},
    {'results': [{'status': 'ERROR'}, {'status': 'SUCCESS'}]},
    {'results': [{'status': 'ERROR'}, {'status': 'ERROR'}]},
    {'requested': 2}, {'requested': True}, {'completed': 3}, {'completed': True},
    {'results': [] , 'completed': 0}, {'results': [{'status': 'unknown'}], 'completed': 1},
    {'atomic': False}, {'atomic': 1}, {'rolled_back': 'yes'},
    {'results': [None], 'completed': 1},
])
def test_batch_validator_rejects_inconsistent_envelopes(updates):
    result = dict(envelope(), **updates)
    with pytest.raises(ApiContractError):
        validate_batch_result(result, 3, True)


def test_batch_validator_rejects_non_atomic_short_and_rollback():
    short = dict(envelope(), atomic=False, rolled_back=False)
    with pytest.raises(ApiContractError, match='every call'):
        validate_batch_result(short, 3, False)
    with pytest.raises(ApiContractError, match='rollback outcome'):
        validate_batch_result(dict(envelope(False), rolled_back=True), 3, False)


@pytest.mark.parametrize('atomic', [True, False])
def test_batch_validator_accepts_full_success_and_rejects_false_error_or_rollback(atomic):
    result = dict(envelope(atomic), status='SUCCESS', completed=3,
                  results=[{'status': 'SUCCESS'}] * 3, rolled_back=False)
    assert validate_batch_result(result, 3, atomic) is result
    with pytest.raises(ApiContractError, match='aggregate status'):
        validate_batch_result(dict(result, status='ERROR'), 3, atomic)
    with pytest.raises(ApiContractError, match='rollback outcome'):
        validate_batch_result(dict(result, rolled_back=True), 3, atomic)


def test_batch_validator_accepts_native_empty_contract():
    result = {'status': 'ERROR', 'error_code': 'BATCH_EMPTY', 'results': []}
    assert validate_batch_result(result, 0, True) is result


@pytest.mark.parametrize('worker_type', [MayaWorker, BlenderWorker])
def test_ipc_rejects_truncated_success(tmp_path, monkeypatch, worker_type):
    worker = ipc_worker(tmp_path, monkeypatch, worker_type)
    result = dict(envelope(), status='SUCCESS', completed=1,
                  results=[{'status': 'SUCCESS'}], rolled_back=False)
    (worker.result_dir / 'test.json').write_text(json.dumps({'task_id': 'test', 'status': 'SUCCESS', 'detail': result}))
    with pytest.raises(ApiContractError, match='aggregate status'):
        worker.run_batch([{'task_id': 'test'}] * 3)


def test_foreground_rejects_truncated_success_without_retry(monkeypatch):
    calls, audits = [], []
    result = dict(envelope(), status='SUCCESS', completed=1,
                  results=[{'status': 'SUCCESS'}], rolled_back=False)
    def run_batch(payloads, *, atomic):
        calls.append(payloads)
        return result
    worker = SimpleNamespace(start=lambda: None, run_batch=run_batch)
    monkeypatch.setattr(foreground_client, '_get_worker', lambda port: (worker, threading.RLock()))
    monkeypatch.setattr(foreground_client, '_write_audit', lambda *args: audits.append(args))
    response = foreground_client.submit_foreground_batch_task({
        'task_id': 'test', 'api_context': {'foreground_port': 7123},
        'api_chain': [{'api_id': 'test'}] * 3})
    assert response['status'] == 'ERROR'
    assert 'aggregate status' in response['detail']
    assert audits[-1][1] == 'ERROR'
    assert len(calls) == 1


@pytest.mark.parametrize('worker_type', [MayaWorker, BlenderWorker])
@pytest.mark.parametrize('response', [
    None, [], {'status': 'SUCCESS', 'detail': ''},
    {'task_id': 'other', 'status': 'SUCCESS', 'detail': ''},
    {'task_id': 'test', 'detail': ''},
    {'task_id': 'test', 'status': 'SUCCESS'},
    {'task_id': 'test', 'status': '', 'detail': ''},
    {'task_id': 'test', 'status': 1, 'detail': ''},
])
def test_ipc_rejects_uncorrelated_or_partial_result(tmp_path, monkeypatch, worker_type, response):
    worker = ipc_worker(tmp_path, monkeypatch, worker_type)
    (worker.result_dir / 'test.json').write_text(json.dumps(response))
    with pytest.raises(ValueError, match='IPC result'):
        worker.run_api({'task_id': 'test', 'api_id': 'test.api'})
    assert json.loads(worker.cmd_file.read_text())['task_id'] == 'test'


@pytest.mark.parametrize('worker_type', [MayaWorker, BlenderWorker])
def test_ipc_bad_json_fails_after_one_command(tmp_path, monkeypatch, worker_type):
    from unittest.mock import patch
    worker = ipc_worker(tmp_path, monkeypatch, worker_type)
    (worker.result_dir / 'test.json').write_text('{"task_id":')
    with patch.object(worker.ipc, 'send', wraps=worker.ipc.send) as send:
        with pytest.raises(json.JSONDecodeError):
            worker.run_api({'task_id': 'test', 'api_id': 'test.api'})
    send.assert_called_once_with({'task_id': 'test', 'api_id': 'test.api'})


def test_file_ipc_atomic_exchange_claim_and_adapter_preserve_pending(tmp_path, monkeypatch):
    worker = FileIpcTransport('worker', tmp_path, clean_stale=True)
    command = {'task_id': 'task', 'api_id': 'test.api'}
    publications = []
    replace = file_ipc.os.replace
    def record(source, target):
        publications.append((source.name, target.name))
        return replace(source, target)
    monkeypatch.setattr(file_ipc.os, 'replace', record)
    worker.send(command)
    adapter = FileIpcTransport('worker', tmp_path)
    assert adapter.claim() == command
    assert adapter.claim() is None
    adapter.publish_result('task', 'SUCCESS', {'value': 1})
    assert worker.exchange(command) == {'task_id': 'task', 'status': 'SUCCESS', 'detail': {'value': 1}}
    assert publications == [('pending.tmp', 'pending.json'), ('pending.json', 'processing.json'),
                            ('task.tmp', 'task.json'), ('pending.tmp', 'pending.json')]
    assert not list(worker.result_dir.iterdir())
    worker.publish_result('stale', 'ERROR')
    FileIpcTransport('worker', tmp_path, clean_stale=True)
    assert not worker.cmd_file.exists() and not list(worker.result_dir.iterdir())


@pytest.mark.parametrize('worker_type', [MayaWorker, BlenderWorker])
@pytest.mark.parametrize('batch', [False, True])
def test_worker_timeout_keeps_kill_policy_and_message(tmp_path, monkeypatch, worker_type, batch):
    worker = ipc_worker(tmp_path, monkeypatch, worker_type)
    ticks, killed = iter((0.0, 2.0)), []
    monkeypatch.setattr(file_ipc.time, 'time', lambda: next(ticks, 2.0))
    monkeypatch.setattr(worker, '_kill_process_tree', lambda: killed.append(True))
    payload = {'task_id': 'task', 'api_id': 'test.api', 'timeout': 1}
    expected = ('Batch task' if batch else 'Task') + ' task IPC timeout after 1s'
    with pytest.raises(TimeoutError, match=expected):
        worker.run_batch([payload]) if batch else worker.run_api(payload)
    assert killed == [True]


@pytest.mark.parametrize('worker_type', [MayaWorker, BlenderWorker])
@pytest.mark.parametrize('timeout', [None, 0, -1])
def test_nonpositive_timeout_waits_without_replay(tmp_path, monkeypatch, worker_type, timeout):
    worker = ipc_worker(tmp_path, monkeypatch, worker_type)
    waits = []
    def respond(interval):
        waits.append(interval)
        worker.ipc.publish_result('task', 'SUCCESS', 'done')
    monkeypatch.setattr(file_ipc.time, 'sleep', respond)
    result = worker.run_api({'task_id': 'task', 'api_id': 'test.api', 'timeout': timeout})
    assert result == {'task_id': 'task', 'status': 'SUCCESS', 'detail': 'done'}
    assert waits == [0.5]


def test_maya_exit_keeps_single_error_and_batch_exception(tmp_path, monkeypatch):
    worker = ipc_worker(tmp_path, monkeypatch, MayaWorker)
    worker.process = SimpleNamespace(poll=lambda: 7, returncode=7)
    payload = {'task_id': 'task', 'api_id': 'test.api'}
    assert worker.run_api(payload) == {
        'status': 'ERROR', 'detail': 'Maya 进程已退出 (exit code: 7)，任务 task 中断'}
    with pytest.raises(RuntimeError, match='Maya 进程已退出'):
        worker.run_batch([payload])


def test_blender_checks_live_process_before_accepting_result(tmp_path, monkeypatch):
    worker = ipc_worker(tmp_path, monkeypatch, BlenderWorker)
    polled = []
    worker.process = SimpleNamespace(poll=lambda: polled.append(True))
    worker.ipc.publish_result('task', 'SUCCESS', 'done')
    assert worker.run_api({'task_id': 'task'})['status'] == 'SUCCESS'
    assert polled == [True]


@pytest.mark.parametrize('batch', [False, True])
def test_blender_exit_ends_unbounded_ipc_wait(tmp_path, monkeypatch, batch):
    worker = ipc_worker(tmp_path, monkeypatch, BlenderWorker)
    exits, waits = iter((None, 17)), []
    worker.process = SimpleNamespace(poll=lambda: next(exits, 17), returncode=17)
    def one_wait(interval):
        waits.append(interval)
        assert len(waits) == 1, 'dead Blender must not keep waiting without a timeout'
    monkeypatch.setattr(file_ipc.time, 'sleep', one_wait)
    payload = {'task_id': 'task', 'api_id': 'test.api', 'timeout': 0}
    if batch:
        with pytest.raises(RuntimeError, match='Blender.*17'):
            worker.run_batch([payload])
    else:
        result = worker.run_api(payload)
        assert result['status'] == 'ERROR'
        assert '17' in result['detail'] and 'task' in result['detail']
    assert waits == [0.5]


@pytest.mark.parametrize('bad_id', ['../escape', '..\\escape', 'C:escape', '..', '', None])
def test_file_ipc_rejects_path_segments_before_publication(tmp_path, bad_id):
    with pytest.raises(ValueError, match='IPC worker_id'):
        FileIpcTransport(bad_id, tmp_path)
    transport = FileIpcTransport('worker', tmp_path)
    with pytest.raises(ValueError, match='IPC task_id'):
        transport.exchange({'task_id': bad_id})
    with pytest.raises(ValueError, match='IPC task_id'):
        transport.publish_result(bad_id, 'SUCCESS')
    assert not transport.cmd_file.exists() and not list(transport.result_dir.iterdir())


def test_file_ipc_close_is_owned_and_idempotent(tmp_path):
    owned = FileIpcTransport('owned', tmp_path)
    sibling = FileIpcTransport('sibling', tmp_path)
    sibling.send({'api_id': '__DIE__'})
    sibling.publish_result('saved', 'SUCCESS')
    owned.close()
    owned.close()
    assert not owned.cmd_file.parent.exists() and not owned.result_dir.exists()
    assert sibling.cmd_file.exists() and (sibling.result_dir / 'saved.json').exists()


def test_file_ipc_close_rejects_resolved_escape(tmp_path, monkeypatch):
    transport = FileIpcTransport('owned', tmp_path)
    resolve = type(tmp_path).resolve
    def escaped(path, *args, **kwargs):
        return tmp_path.parent if path == transport.cmd_file.parent else resolve(path, *args, **kwargs)
    monkeypatch.setattr(type(tmp_path), 'resolve', escaped)
    with pytest.raises(ValueError, match='escapes worker ownership'):
        transport.close()
    assert transport.cmd_file.parent.exists() and transport.result_dir.exists()


def test_warm_shutdown_keeps_transport_until_real_recycle(tmp_path, monkeypatch):
    from cgi_pipeline.core import dcc_factory
    worker = ipc_worker(tmp_path, monkeypatch, MayaWorker)
    worker._scene_workspace_id = 'active'
    pool = {'maya': {'worker': worker, 'task_count': 51}}
    monkeypatch.setattr(dcc_factory, '_warm_pool', pool)
    proxy = WarmWorkerProxy.__new__(WarmWorkerProxy)
    proxy.dcc_type, proxy.real_worker = 'maya', worker
    proxy.shutdown()
    assert worker.cmd_file.parent.exists() and worker.result_dir.exists()
    worker._scene_workspace_id = None
    proxy.shutdown()
    assert not worker.cmd_file.parent.exists() and not worker.result_dir.exists()
    assert not pool
