import sys
import threading
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cgi_pipeline.hosts.blender import foreground_client
from cgi_pipeline.server import internals


def test_generated_foreground_code_passes_host_context(monkeypatch):
    """Execute the transport payload, not only a mocked success envelope."""
    from cgi_pipeline import execution
    bpy = SimpleNamespace(data=SimpleNamespace(objects=[]))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    payload = {
        'api_id': 'blender.scene.nodes.find',
        'api_params': {'patterns': ['*'], 'max_items': 3},
        'api_context': {'project': 'test-project'},
    }
    with patch.object(execution, 'execute_api', return_value={'status': 'SUCCESS'}) as run:
        namespace = {}
        exec(foreground_client._api_code(payload, 9876), namespace)
    context = run.call_args.kwargs['context']
    assert context['bpy_module'] is bpy
    assert context['foreground_port'] == 9876
    assert context['execution_mode'] == 'foreground'
    assert context['project'] == 'test-project'
    assert run.call_args.kwargs['params'] == payload['api_params']
    assert payload['api_context'] == {'project': 'test-project'}


def test_native_response_preserves_result_stdout_and_stderr():
    payload = {
        "task_id": "echo-test",
        "api_id": "blender.asset.blender_exec_code",
        "api_params": {"code": "result = {'value': 42}"},
        "api_context": {"execution_mode": "foreground", "foreground_port": 9876},
    }
    response = {
        "status": "ok",
        "result": {
            "api_id": payload["api_id"],
            "api_version": "1.0.0",
            "status": "SUCCESS",
            "input": {"code": payload["api_params"]["code"]},
            "output": {"result": {"value": 42}},
            "elapsed_sec": 0.01,
            "error_code": "",
            "error": "",
            "recovery_hint": "",
            "artifacts": [],
        },
        "stdout": "stdout marker\n",
        "stderr": "stderr marker\n",
    }
    with patch.object(foreground_client, "request", return_value=response):
        result = foreground_client.submit_foreground_task(payload, sync=True)
    detail = result["detail"]
    assert result["status"] == "SUCCESS"
    assert detail["output"]["result"] == {"value": 42}
    assert detail["stdout"] == "stdout marker\n"
    assert detail["stderr"] == "stderr marker\n"


def test_native_error_becomes_error_receipt_with_traceback():
    payload = {
        "task_id": "error-test",
        "api_id": "blender.asset.blender_exec_code",
        "api_params": {"code": "raise RuntimeError('boom')"},
        "api_context": {"execution_mode": "foreground", "foreground_port": 9876},
    }
    response = {
        "status": "error",
        "message": "Traceback (most recent call last):\nRuntimeError: boom\n",
        "stdout": "before failure\n",
    }
    with patch.object(foreground_client, "request", return_value=response):
        result = foreground_client.submit_foreground_task(payload, sync=True)
    detail = result["detail"]
    assert result["status"] == "ERROR"
    assert detail["stdout"] == "before failure\n"
    assert detail["error_type"] == "RuntimeError"
    assert detail["error"] == "boom"
    assert "Traceback" in detail["traceback"]


def test_foreground_api_executes_runner_inside_blender():
    payload = {
        "task_id": "api-test",
        "api_id": "blender.asset.blender_exec_code",
        "api_params": {"code": "result = {}", "description": "api test"},
        "api_context": {"execution_mode": "foreground", "foreground_port": 9876},
    }
    response = {
        "status": "ok",
        "result": {
            "api_id": payload["api_id"],
            "api_version": "1.0.0",
            "status": "SUCCESS",
            "input": payload["api_params"],
            "output": {},
            "elapsed_sec": 0.01,
            "error_code": "",
            "error": "",
            "recovery_hint": "",
            "artifacts": [],
        },
    }
    with patch.object(foreground_client, "request", return_value=response) as request:
        result = foreground_client.submit_foreground_task(payload, sync=True)
    sent_code = request.call_args.args[1]
    assert "from cgi_pipeline.execution import execute_api" in sent_code
    assert payload["api_id"] in sent_code
    assert result["status"] == "SUCCESS"


def test_api_error_promotes_its_traceback_to_the_receipt():
    payload = {
        "task_id": "api-error-test",
        "api_id": "blender.asset.blender_exec_code",
        "api_params": {"code": "raise RuntimeError('boom')"},
        "api_context": {"execution_mode": "foreground", "foreground_port": 9876},
    }
    response = {
        "status": "ok",
        "result": {
            "api_id": payload["api_id"],
            "api_version": "1.0.0",
            "status": "ERROR",
            "input": payload["api_params"],
            "output": {},
            "elapsed_sec": 0.01,
            "error_code": "API_EXECUTION_ERROR",
            "error": "boom",
            "recovery_hint": "Traceback (most recent call last): RuntimeError: boom",
            "artifacts": [],
        },
    }
    with patch.object(foreground_client, "request", return_value=response):
        result = foreground_client.submit_foreground_task(payload, sync=True)
    assert result["status"] == "ERROR"
    assert "RuntimeError: boom" in result["detail"]["traceback"]


def test_blender_foreground_still_routes_to_blender_client():
    payload = {"code": "result = {}"}
    expected = {"status": "SUCCESS", "detail": {"output": {"result": {}}}}
    with patch(
        "cgi_pipeline.hosts.blender.foreground_client.submit_foreground_task",
        return_value=expected,
    ) as submit:
        actual = internals._submit_api_to_celery(
            "blender_exec_code",
            {
                "params": payload,
                "context": {
                    "execution_mode": "foreground",
                    "foreground_port": 9876,
                },
            },
        )
    assert actual == expected
    submit.assert_called_once()


def _health_response(port=9876, **overrides):
    return {
        'status': 'ok',
        'result': {
            'bridge': 'cgi_pipeline', 'protocol_version': 1, 'dcc': 'blender',
            'pid': 123, 'file': '/scene.blend', 'blender_version': '5.0.1',
            'port': port, **overrides,
        },
    }


def test_request_confirms_native_identity_before_execute():
    response = {'status': 'ok', 'result': 42, 'stdout': 'hello\n'}
    with patch.object(foreground_client.native_bridge, 'send_request',
                      side_effect=[_health_response(), response]) as send:
        actual = foreground_client.request(9876, 'result = 42', timeout=300)
    assert actual == response
    assert send.call_args_list[0].args == (9876, {'type': 'health'})
    assert send.call_args_list[0].kwargs['timeout'] == foreground_client.DEFAULT_DISCOVERY_TIMEOUT
    assert send.call_args_list[1].args == (9876, {'type': 'execute', 'code': 'result = 42'})
    assert send.call_args_list[1].kwargs['timeout'] == 300


@pytest.mark.parametrize('response', [
    _health_response(bridge='blender_mcp'),
    _health_response(protocol_version=2),
    _health_response(dcc='maya'),
    {'status': 'error', 'message': 'unrecognized command'},
    {'status': 'ok', 'result': None},
])
def test_wrong_endpoint_never_receives_user_code(response):
    with patch.object(foreground_client.native_bridge, 'send_request', return_value=response) as send:
        with pytest.raises(foreground_client.BlenderBridgeError, match='not a compatible'):
            foreground_client.request(9876, 'raise RuntimeError("must not run")', timeout=300)
    send.assert_called_once_with(9876, {'type': 'health'}, timeout=foreground_client.DEFAULT_DISCOVERY_TIMEOUT)


def test_health_transport_error_becomes_submit_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(foreground_client, 'AUDIT_DIR', tmp_path)
    error = foreground_client.native_bridge.NativeBridgeError('socket timed out')
    with patch.object(foreground_client.native_bridge, 'send_request', side_effect=error) as send:
        result = foreground_client.submit_foreground_task({
            'task_id': 'failed-health', 'api_context': {'foreground_port': 9876},
            'api_params': {'code': 'result = 42'},
        }, sync=True)
    assert result['status'] == 'SUBMIT_FAILED'
    assert result['detail']['error_type'] == 'ConnectionError'
    assert result['detail']['error'] == 'socket timed out'
    assert send.call_count == 1
    assert list(tmp_path.rglob('failed-health.json'))


@pytest.mark.parametrize('port', [0, 65536, -1, None, True, 9876.5, 'invalid'])
def test_request_rejects_invalid_port_before_transport(port):
    with patch.object(foreground_client.native_bridge, 'send_request') as send:
        with pytest.raises(foreground_client.BlenderBridgeError, match='Invalid Blender foreground port'):
            foreground_client.request(port, 'result = 42')
    send.assert_not_called()


def test_discovery_scans_ports_concurrently_without_executing_code(monkeypatch):
    ports = [9876, 9877, 9878]
    monkeypatch.setattr(foreground_client, 'blender_ports', lambda: ports)
    barrier = threading.Barrier(len(ports))

    def health(port, message, timeout):
        assert message == {'type': 'health'}
        assert timeout == 0.2
        barrier.wait(timeout=2)
        return _health_response(port) if port != 9877 else _health_response(port, bridge='other')

    with patch.object(foreground_client.native_bridge, 'send_request', side_effect=health):
        sessions = foreground_client.discover_blender_sessions(timeout=0.2)
    assert [session['port'] for session in sessions] == [9876, 9878]
    assert all(session['file'] == '/scene.blend' for session in sessions)


def test_port_scan_combines_normalized_range_and_custom_ports(monkeypatch):
    monkeypatch.setenv('BLENDER_FOREGROUND_PORT_START', '9878')
    monkeypatch.setenv('BLENDER_FOREGROUND_PORT_END', '9876')
    monkeypatch.setenv('BLENDER_FOREGROUND_PORTS', ' 10001,9877,10002,, ')
    assert foreground_client.blender_ports() == [9876, 9877, 9878, 10001, 10002]


@pytest.mark.parametrize('name,value', [
    ('BLENDER_FOREGROUND_PORT_START', '0'),
    ('BLENDER_FOREGROUND_PORT_END', '65536'),
    ('BLENDER_FOREGROUND_PORTS', '10001,bad'),
])
def test_scan_configuration_rejects_invalid_ports(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(foreground_client.BlenderBridgeError):
        foreground_client.blender_ports()


def test_auto_selection_requires_one_native_session():
    with patch.object(foreground_client, 'discover_blender_sessions', return_value=[]):
        with pytest.raises(foreground_client.BlenderBridgeError) as error:
            foreground_client._resolve_port({})
        assert error.value.error_type == 'NoForegroundSession'
    sessions = [_health_response(9876)['result'], _health_response(9877)['result']]
    with patch.object(foreground_client, 'discover_blender_sessions', return_value=sessions):
        with pytest.raises(foreground_client.BlenderBridgeError) as error:
            foreground_client._resolve_port({})
        assert error.value.error_type == 'MultipleForegroundSessions'
        assert error.value.response['active_sessions'] == sessions
        assert foreground_client._resolve_port({'foreground_port': '9877'}) == 9877
