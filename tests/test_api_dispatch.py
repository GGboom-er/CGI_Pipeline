from __future__ import annotations

import json
import pytest
from unittest.mock import patch

from cgi_pipeline.server import internals
from cgi_pipeline.core import tasks
from cgi_pipeline.core.tasks import _receipt_output, _step_executor, _step_id
from test_workspace import root


class _FakeCelery:
    def __init__(self):
        self.calls = []

    def send_task(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return object()


def test_api_background_reuses_worker_task_and_api_payload(tmp_path, root):
    celery = _FakeCelery()
    with patch.object(internals, "AUDIT_DIR", tmp_path), \
         patch.object(internals, "_ensure_worker", return_value=(True, "ok")), \
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
    assert payload["api_context"]["task_id"] == payload["task_id"]


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


def test_workflow_chain_dispatches_only_canonical_worker_fields(tmp_path):
    captured = []

    class _FakeWorker:
        def run_api(self, payload):
            captured.append(payload)
            return {
                "status": "SUCCESS",
                "detail": json.dumps({
                    "api_id": "pipeline.pipeline.resolve_asset_files",
                    "status": "SUCCESS",
                    "output": {"source_path": "source.blend"},
                }),
            }

    payload = {
        "task_id": "chain-transport-test",
        "workflow_id": "chain-transport-test",
        "project": "ysj",
        "asset_name": "hero",
        "source_path": "",
        "run_dir": str(tmp_path),
        "suppress_report": True,
        "api_chain": [{
            "api_id": "pipeline.pipeline.resolve_asset_files",
            "parameters": {"category": "chr"},
        }],
    }

    with patch("cgi_pipeline.core.tasks._create_worker", return_value=_FakeWorker()), \
         patch("cgi_pipeline.core.tasks._get_audit_path", return_value=tmp_path / "audit.json"), \
         patch("cgi_pipeline.core.tasks._open_source_file", return_value=(True, '')), \
         patch("cgi_pipeline.core.tasks._update_progress"), \
         patch("cgi_pipeline.core.tasks.publish_event"):
        result = tasks.execute_api_chain.run(payload)

    assert result["status"] == "SUCCESS", result
    assert len(captured) == 1
    step_payload = captured[0]
    assert step_payload["api_params"] == {"category": "chr"}
    assert step_payload["api_context"]["execution_mode"] == "background"
    assert not ({"params", "parameters", "context"} & set(step_payload))


def test_workflow_chain_save_params_exclude_orchestration_metadata(tmp_path):
    captured = []

    class _FakeWorker:
        def run_api(self, payload):
            captured.append(payload)
            return {
                "status": "SUCCESS",
                "detail": json.dumps({
                    "api_id": payload["api_id"],
                    "status": "SUCCESS",
                    "output": {"output_path": "sandbox.ma"},
                }),
            }

    save_path = str(tmp_path / "sandbox.ma")
    payload = {
        "task_id": "chain-save-transport-test",
        "workflow_id": "chain-save-transport-test",
        "project": "ysj",
        "asset_name": "hero",
        "source_path": "",
        "run_dir": str(tmp_path),
        "suppress_report": True,
        "api_chain": [
            {
                "api_id": "maya.asset.save_scene",
                "parameters": {"save_path": str(tmp_path / "before.ma")},
            },
            {
                "api_id": "maya.asset.save_scene",
                "parameters": {"save_path": save_path},
            },
        ],
    }

    with patch("cgi_pipeline.core.tasks._create_worker", return_value=_FakeWorker()), \
         patch("cgi_pipeline.core.tasks._get_audit_path", return_value=tmp_path / "audit.json"), \
         patch("cgi_pipeline.core.tasks._open_source_file", return_value=(True, '')), \
         patch("cgi_pipeline.core.tasks._update_progress"), \
         patch("cgi_pipeline.core.tasks.publish_event"):
        result = tasks.execute_api_chain.run(payload)

    assert result["status"] == "SUCCESS", result
    save_payloads = [item for item in captured if item.get("api_id") == "maya.asset.save_scene"]
    assert len(save_payloads) == 2
    save_payload = save_payloads[-1]
    assert save_payload["api_params"] == {"save_path": save_path}
    assert "_chain_history" not in save_payload["api_params"]
    assert "_open_elapsed_sec" not in save_payload["api_params"]


@pytest.mark.parametrize('atomic', [False, True])
@pytest.mark.parametrize('native', [False, True])
def test_batch_records_every_executed_step_without_replaying_stopped_calls(tmp_path, atomic, native):
    receipts = [
        {'api_id': 'maya.scene.nodes.resolve', 'status': status, 'output': {}}
        for status in ('SUCCESS', 'ERROR', 'SUCCESS')
    ][:2 if atomic else 3]
    envelope = {'status': 'ERROR', 'results': receipts, 'completed': len(receipts),
                'requested': 3, 'atomic': atomic, 'rolled_back': atomic}

    calls = []
    class Worker:
        def run_api(self, payload):
            # Source initialization may occur, but no batch item can be replayed.
            if payload['api_id'] == 'maya.scene.nodes.resolve':
                assert not native
                calls.append(payload)
                return receipts[len(calls) - 1]
            return {'status': 'SUCCESS', 'detail': '{}'}

    if native:
        def run_batch(self, payloads, *, atomic):
            assert len(payloads) == 3
            assert atomic == envelope['atomic']
            return envelope
        Worker.run_batch = run_batch
    else:
        envelope['rolled_back'] = False

    audit = tmp_path / 'audit.json'
    payload = {'task_id': 'batch-policy', 'project': 'default', 'asset_name': 'test',
               'run_dir': str(tmp_path), 'suppress_report': True, 'batch_mode': True,
               'atomic': atomic, 'api_chain': [
                   {'api_id': 'maya.scene.nodes.resolve', 'parameters': {}} for _ in range(3)]}
    with patch.object(tasks, '_create_worker', return_value=Worker()), \
         patch.object(tasks, '_get_audit_path', return_value=audit), \
         patch.object(tasks, '_open_source_file', return_value=(True, '')), \
         patch.object(tasks, '_update_progress'), patch.object(tasks, 'publish_event'):
        result = tasks.execute_api_chain.run(payload)
    assert result['status'] == 'CHAIN_ABORTED'
    assert [r['status'] for r in result['chain_results']] == [r['status'] for r in receipts]
    assert result['batch'] == {k: v for k, v in envelope.items() if k != 'results'}
    entries = [json.loads(line) for line in audit.read_text().splitlines()]
    assert len([e for e in entries if e['status'] in ('STEP_SUCCESS', 'STEP_ERROR')]) == len(receipts)
    assert json.loads(next(e['detail'] for e in entries if e['status'] == 'BATCH_RESULT')) == result['batch']
