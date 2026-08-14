from __future__ import annotations

from unittest.mock import patch

from cgi_pipeline.server import internals
from cgi_pipeline.core.tasks import _receipt_output, _step_executor, _step_id


class _FakeCelery:
    def __init__(self):
        self.calls = []

    def send_task(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()


def test_api_background_reuses_worker_task_and_api_payload():
    celery = _FakeCelery()
    with patch.object(internals, "_ensure_worker", return_value=(True, "ok")), \
         patch("cgi_pipeline.core.service_manager.start_redis", return_value=True), \
         patch("cgi_pipeline.core.service_manager.get_celery_app", return_value=celery):
        result = internals._submit_api_to_celery(
            "maya.rig.reference.update",
            {
                "project": "ysj",
                "asset_name": "hero",
                "source_path": "C:/hero.ma",
                "params": {"operation": "preview", "target_map": []},
                "context": {"execution_mode": "background"},
            },
        )

    assert result["status"] == "SUBMITTED"
    assert result["api_id"] == "maya.rig.reference.update"
    args, kwargs = celery.calls[0]
    assert args[0] == "cgi_pipeline.core.tasks.execute_api_operation"
    payload = kwargs["args"][0]
    assert payload["api_id"] == "maya.rig.reference.update"
    assert payload["api_id"] == "maya.rig.reference.update"
    assert payload["api_params"]["operation"] == "preview"
    assert not ({"params", "parameters", "context"} & set(payload))
    assert kwargs["queue"] == "cgi_queue"


def test_api_foreground_uses_existing_maya_foreground_client():
    with patch(
        "cgi_pipeline.server.foreground_client.submit_foreground_task",
        return_value={"status": "SUCCESS", "task_id": "api-test"},
    ) as submit:
        result = internals._submit_api_to_celery(
            "maya.rig.reference.update",
            {
                "params": {"operation": "preview", "target_map": []},
                "context": {
                    "execution_mode": "foreground",
                    "foreground_port": 7009,
                    "sync": True,
                },
            },
        )

    assert result["status"] == "SUCCESS"
    payload = submit.call_args.args[0]
    assert payload["api_id"] == "maya.rig.reference.update"
    assert not ({"params", "parameters", "context"} & set(payload))
    assert payload["api_context"]["foreground_port"] == 7009
    assert payload["api_context"]["execution_mode"] == "foreground"


def test_api_contract_rejects_missing_inputs_before_worker_start():
    with patch.object(internals, "_ensure_worker") as ensure_worker:
        result = internals._submit_api_to_celery(
            "maya.rig.reference.update",
            {"params": {"operation": "preview"}},
        )
    assert result["status"] == "ERROR"
    assert result["error_code"] == "API_CONTRACT_ERROR"
    ensure_worker.assert_not_called()


def test_workflow_step_helpers_accept_api_nodes():
    step = {"api_id": "maya.rig.reference.update", "parameters": {}}
    assert _step_id(step) == "maya.rig.reference.update"
    assert _step_executor(step) == "maya"


def test_workflow_projects_canonical_receipt_output():
    receipt = {
        "api_id": "pipeline.session.discover",
        "status": "SUCCESS",
        "output": {"selected_session": {"dcc": "maya", "port": 7009}},
    }

    assert _receipt_output(receipt) == receipt["output"]
    assert "outputs" not in receipt
