import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cgi_pipeline.hosts.blender import foreground_client
from cgi_pipeline.server import internals


def test_official_response_preserves_result_stdout_and_stderr():
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


def test_official_error_becomes_error_receipt_with_traceback():
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
