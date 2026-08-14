from __future__ import annotations

import json
from unittest.mock import patch

from cgi_pipeline.core.pipeline_worker import PipelineWorker


def test_pipeline_worker_uses_catalog_runner_for_dotted_api_id():
    receipt = {
        "api_id": "pipeline.pipeline.resolve_asset_files",
        "api_version": "1.0.0",
        "status": "SUCCESS",
        "input": {},
        "output": {},
        "elapsed_sec": 0.01,
        "error_code": "",
        "error": "",
        "recovery_hint": "",
        "artifacts": [],
    }
    payload = {
        "task_id": "pipeline-worker-test",
        "api_id": "pipeline.pipeline.resolve_asset_files",
        "api_params": {"category": "chr"},
        "project": "ysj",
        "asset_name": "ciweiguai",
        "source_path": "",
        "run_dir": "C:/runs/pipeline-worker-test",
        "extra_params": {"info_dir": "C:/runs/pipeline-worker-test/info"},
    }

    with patch("cgi_pipeline.core.pipeline_worker.execute_api", return_value=receipt) as execute:
        result = PipelineWorker().run_api(payload)

    execute.assert_called_once_with(
        "pipeline.pipeline.resolve_asset_files",
        {"category": "chr"},
        {
            "execution_mode": "background",
            "source_path": "",
            "project": "ysj",
            "asset_name": "ciweiguai",
            "run_dir": "C:/runs/pipeline-worker-test",
            "task_id": "pipeline-worker-test",
            "extra_params": {"info_dir": "C:/runs/pipeline-worker-test/info"},
        },
    )
    assert result["task_id"] == "pipeline-worker-test"
    assert result["status"] == "SUCCESS"
    assert json.loads(result["detail"]) == receipt


def test_pipeline_worker_rejects_removed_transport_fields():
    payload = {
        "task_id": "removed-transport-test",
        "api_id": "pipeline.pipeline.resolve_asset_files",
        "parameters": {"category": "chr"},
    }

    with patch("cgi_pipeline.core.pipeline_worker.execute_api") as execute:
        result = PipelineWorker().run_api(payload)

    execute.assert_not_called()
    assert result["status"] == "ERROR"
    assert "removed API transport fields: parameters" in json.loads(result["detail"])["error"]
