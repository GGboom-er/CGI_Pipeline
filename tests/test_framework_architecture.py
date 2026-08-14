"""Architecture contract for the unified CGI Pipeline framework."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import yaml
from unittest.mock import patch

from cgi_pipeline.capabilities.maya.rig.maya_sync_rig_incremental.sync_contract import (
    parse_sync_inputs,
    validate_compare_result_payload,
)
from cgi_pipeline.capabilities.pipeline.pipeline.write_task_report import write_task_report
from cgi_pipeline.core import service_manager, task_status
from cgi_pipeline.core.task_report_writer import _block_markers
from cgi_pipeline.contracts import ApiContractError, validate_params


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def test_public_namespace_is_host_safe():
    sys.path.insert(0, str(SRC))
    try:
        for name in ("cgi_pipeline", "celery", "redis", "fastmcp", "mcp"):
            sys.modules.pop(name, None)
        module = importlib.import_module("cgi_pipeline")
        assert module.__all__ == ["api", "client", "tools"]
        assert not ({"celery", "redis", "fastmcp", "mcp"} & set(sys.modules))
    finally:
        sys.path.remove(str(SRC))


def test_generated_capability_catalog_uses_professional_facets():
    catalog_path = SRC / "cgi_pipeline" / "data" / "capabilities.json"
    rows = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert len(rows) >= 44
    required = {
        "api_id",
        "executor",
        "capability",
        "operation",
        "stages",
        "targets",
        "access",
        "effects",
        "execution_modes",
        "surfaces",
    }
    assert all(required <= set(row) for row in rows)
    assert all(not ({"dcc", "domain", "action", "tier"} & set(row)) for row in rows)


def test_package_catalog_resolves_adpose_without_copying_source():
    package_path = SRC / "cgi_pipeline" / "data" / "packages.json"
    rows = json.loads(package_path.read_text(encoding="utf-8"))
    adpose = next(row for row in rows if row["package_id"] == "adpose")
    assert adpose["source"]["type"] == "workspace"
    assert Path(adpose["source"]["path"]).resolve() == Path("Y:/GGbommer/scripts/adPose").resolve()
    assert adpose["entrypoints"]["maya"] == "adPose.ui:show_in_maya"


def test_maya_module_exposes_only_the_public_sdk_path():
    module_file = ROOT / "deploy" / "maya" / "modules" / "CGIPipeline.mod"
    text = module_file.read_text(encoding="utf-8")
    assert "+ CGIPipeline 1.0 ../../.." in text
    assert "PYTHONPATH +:=src" in text
    assert "PYTHONPATH +:=." not in text


def test_framework_exposes_one_root_worker_receipt_and_report_contract():
    bootstrap = importlib.import_module("cgi_pipeline.core.bootstrap")
    assert not hasattr(bootstrap, "PROJECT_ROOT")
    with patch.object(service_manager, "is_redis_alive", return_value=False), \
         patch.object(service_manager, "is_worker_alive", return_value=(False, None)):
        assert set(service_manager.get_service_status()) == {"redis", "worker_cgi", "dashboard"}
    assert "CHAIN_HELD" not in task_status.TERMINAL_STATUSES
    assert not hasattr(task_status, "HELD_STATUSES")
    assert not hasattr(task_status, "is_held")
    assert _block_markers("step") == (
        "<!-- report:block:start step -->",
        "<!-- report:block:end step -->",
    )


def test_capability_manifests_reject_removed_parameter_aliases():
    manifests = {
        "maya_compare": SRC / "cgi_pipeline" / "capabilities" / "maya" / "asset" / "maya_compare_asset_in_scene" / "capability.yaml",
        "pipeline_compare": SRC / "cgi_pipeline" / "capabilities" / "pipeline" / "pipeline" / "pipeline_compare_asset" / "capability.yaml",
        "maya_sync": SRC / "cgi_pipeline" / "capabilities" / "maya" / "rig" / "maya_sync_rig_incremental" / "capability.yaml",
    }
    inputs = {
        name: set(yaml.safe_load(path.read_text(encoding="utf-8"))["inputs"])
        for name, path in manifests.items()
    }
    assert not ({"source_abc", "source_info"} & inputs["maya_compare"])
    assert not ({"input_a", "input_b", "label_a", "label_b"} & inputs["pipeline_compare"])
    assert "dry_run" not in inputs["maya_sync"]


def test_sync_contract_uses_only_explicit_source_parameters():
    inputs = parse_sync_inputs({
        "source_path": "rig.ma",
        "parameters": {
            "compare_result": {"schema_version": "compare_result.v1"},
            "source_abc": "source.abc",
            "abc_path": "ignored.abc",
            "tex_json": "ignored.json",
        },
    })
    assert inputs.source_abc == "source.abc"
    assert inputs.source_info == ""


def test_compare_result_contract_does_not_require_embedded_source_info():
    report = validate_compare_result_payload({
        "schema_version": "compare_result.v1",
        "compare": {"pairing_groups": [], "target_only_dags": []},
    })
    assert report == {"pairing_groups": [], "target_only_dags": []}


def test_report_writer_rejects_unknown_mode_before_filesystem_work():
    receipt = write_task_report.execute({
        "task_id": "invalid-mode",
        "parameters": {"mode": "hold"},
    })
    assert receipt["status"] == "ERROR"
    assert "normal 或 workflow" in receipt["error"]


def test_api_contract_rejects_unregistered_inputs():
    try:
        validate_params({"inputs": {"current": {"type": "string"}}}, {"legacy": "value"})
    except ApiContractError as exc:
        assert str(exc) == "unknown API inputs: legacy"
    else:
        raise AssertionError("unregistered API input was accepted")
