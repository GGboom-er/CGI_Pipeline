from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cgi_pipeline.core import tasks, workflow_engine


def test_tex_to_rig_profiles_expand_from_one_ten_step_fragment():
    raw = workflow_engine.load_workflow("tex_to_rig_verify_and_sync")
    sync = workflow_engine.compile_workflow(raw, {"mode": "geometry", "save_result": True})
    full = workflow_engine.compile_workflow(raw, {"mode": "geometry_materials", "save_result": True})
    verify = workflow_engine.compile_workflow(raw, {})

    assert len(sync["steps"]) == 10
    assert [step["step_id"] for step in sync["steps"]] == [
        "resolve_files",
        "export_abc",
        "check_hierarchy_pre",
        "fix_hierarchy_pre",
        "compare_pre",
        "sync",
        "check_hierarchy_post",
        "fix_shapes",
        "verify",
        "save",
    ]
    common_full = [
        step for step in full["steps"]
        if step["step_id"] not in {"extract_materials", "assign_materials"}
    ]
    assert common_full == sync["steps"]
    assert [step["step_id"] for step in full["steps"]] == [
        "resolve_files", "export_abc", "extract_materials", "check_hierarchy_pre",
        "fix_hierarchy_pre", "compare_pre", "sync", "check_hierarchy_post",
        "fix_shapes", "assign_materials", "verify", "save",
    ]
    assert [step["step_id"] for step in verify["steps"]] == [
        "resolve_files", "export_abc", "check_hierarchy_pre", "fix_hierarchy_pre", "compare_pre",
    ]
    compare = next(step for step in verify['steps'] if step['step_id']=='compare_pre')
    assert verify['steps'][2]["source_path"] == "{{outputs.resolve_files.result.rig_path}}"
    assert compare["parameters"]["cache_group"] == "{{outputs.fix_hierarchy_pre.result.active_rig_root}}"

    public_ids = {item["workflow_id"] for item in workflow_engine.list_workflows()}
    assert "tex_to_rig_geometry" not in public_ids
    assert public_ids == {"blender_to_maya_full_build", "tex_to_rig_verify_and_sync"}


def test_post_hierarchy_declares_one_repair_then_retry():
    workflow = workflow_engine.compile_workflow(workflow_engine.load_workflow("tex_to_rig_verify_and_sync"), {"mode": "geometry"})
    step = next(item for item in workflow["steps"] if item["step_id"] == "check_hierarchy_post")
    assert step["parameters"]["block_on_fail"] is True
    assert step["failure_policy"] == {
        "action": "repair_then_retry",
        "repair_step": {
            "api_id": "maya.rig.maya_fix_asset_hierarchy",
            "parameters": {
                "check_result": "{{outputs.__failed__.result}}",
                "delete_extra_top_nodes": False,
            },
        },
        "max_attempts": 1,
        "on_exhausted": "needs_attention",
    }


def _receipt(api_id: str, status: str, output: dict | None = None) -> dict:
    return {
        "api_id": api_id,
        "status": status,
        "output": output or {},
        "error": "fixture failure" if status != "SUCCESS" else "",
    }


def _run_chain(tmp_path, steps, results):
    calls = []

    class Worker:
        def run_api(self, payload):
            calls.append(payload)
            result = results[len(calls) - 1]
            return {
                "status": result["status"],
                "detail": json.dumps(result),
            }

    payload = {
        "task_id": "failure-policy-test",
        "workflow_id": "failure-policy-test",
        "project": "default",
        "asset_name": "fixture",
        "source_path": "",
        "run_dir": str(tmp_path),
        "suppress_report": True,
        "api_chain": steps,
    }
    with patch.object(tasks, "_create_worker", return_value=Worker()), \
         patch.object(tasks, "_get_audit_path", return_value=tmp_path / "audit.jsonl"), \
         patch.object(tasks, "_update_progress"), \
         patch.object(tasks, "publish_event"):
        return tasks.execute_api_chain.run(payload), calls


def test_repair_then_retry_recovers_once(tmp_path):
    check_id = "pipeline.pipeline.resolve_asset_files"
    repair_id = "pipeline.pipeline.resolve_asset_files"
    steps = [{
        "step_id": "check",
        "api_id": check_id,
        "parameters": {},
        "failure_policy": {
            "action": "repair_then_retry",
            "repair_step": {
                "api_id": repair_id,
                "parameters": {"check_result": "{{outputs.__failed__.result}}"},
            },
            "max_attempts": 1,
            "on_exhausted": "needs_attention",
        },
    }]
    result, calls = _run_chain(tmp_path, steps, [
        _receipt(check_id, "AUDIT_FAILED", {"result": {"issue": "minor"}}),
        _receipt(repair_id, "SUCCESS", {"fixed": True}),
        _receipt(check_id, "SUCCESS", {"result": {"passed": True}}),
    ])

    assert result["status"] == "SUCCESS"
    assert [item["task_id"] for item in calls] == [
        "failure-policy-test_s0",
        "failure-policy-test_s0_repair",
        "failure-policy-test_s0",
    ]
    assert calls[1]["api_params"]["check_result"] == {"issue": "minor"}
    recovery = result["chain_results"][0]["failure_policy"]
    assert recovery["initial_status"] == "AUDIT_FAILED"
    assert recovery["repair_status"] == "SUCCESS"
    assert recovery["retry_status"] == "SUCCESS"
    assert recovery["recovered"] is True


@pytest.mark.parametrize(
    "action,expected_status,expected_calls",
    [
        ("abort", "CHAIN_ABORTED", 1),
        ("needs_attention", "CHAIN_NEEDS_ATTENTION", 1),
        ("warn_continue", "SUCCESS_WITH_WARNINGS", 2),
    ],
)
def test_explicit_failure_actions(tmp_path, action, expected_status, expected_calls):
    steps = [
        {
            "step_id": "first",
            "api_id": "pipeline.pipeline.resolve_asset_files",
            "parameters": {},
            "failure_policy": {"action": action},
        },
        {
            "step_id": "second",
            "api_id": "pipeline.pipeline.resolve_asset_files",
            "parameters": {},
        },
    ]
    result, calls = _run_chain(tmp_path, steps, [
        _receipt("pipeline.pipeline.resolve_asset_files", "ERROR"),
        _receipt("pipeline.pipeline.resolve_asset_files", "SUCCESS"),
    ])

    assert result["status"] == expected_status
    assert len(calls) == expected_calls
    if action == "warn_continue":
        assert result["warnings"][0]["action"] == "warn_continue"
