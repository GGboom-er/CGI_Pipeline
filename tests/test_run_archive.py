# -*- coding: utf-8 -*-
# tests/test_run_archive.py
# 验证任务沙盒命名与同一 task 的沙盒稳定映射。

import shutil
import sys
import re
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.run_archive import (
    create_run_dir,
    get_run_report_path,
    write_manifest,
    cleanup_root_machine_duplicates,
)


def _clean_project(project: str):
    path = ROOT / "projects" / project
    if path.exists():
        shutil.rmtree(path)


def test_readable_sandbox_name_and_report_path():
    project = "_test_run_archive"
    _clean_project(project)
    submitted_at = 1_714_000_000
    run_dir = create_run_dir("wf-readable-001", project, "mihouwang", submitted_at)
    assert re.match(r"^\d{8}_\d{6}_mihouwang$", run_dir.name), run_dir
    assert run_dir.name.endswith("_mihouwang"), run_dir
    assert "wf-readable-001" not in run_dir.name, run_dir
    assert (run_dir / ".info" / "run_state.json").exists()
    assert get_run_report_path("wf-readable-001", "mihouwang", project).endswith("REPORT.md")
    print(f"✓ test_readable_sandbox_name_and_report_path → {run_dir}")


def test_same_second_collision_suffix():
    project = "_test_run_archive_collision"
    _clean_project(project)
    submitted_at = 1_714_000_000
    first = create_run_dir("wf-collision-001", project, "mihouwang", submitted_at)
    second = create_run_dir("wf-collision-002", project, "mihouwang", submitted_at)
    assert first != second
    assert second.name.endswith("_01"), second
    again = create_run_dir("wf-collision-001", project, "mihouwang", submitted_at)
    assert again == first
    print(f"✓ test_same_second_collision_suffix → {first}, {second}")


def test_manifest_reports_exclude_machine_outputs():
    project = "_test_run_archive_reports"
    _clean_project(project)
    run_dir = create_run_dir("wf-reports-001", project, "mihouwang", 1_714_000_100)
    report = run_dir / "REPORT.md"
    report.write_text("# report\n", encoding="utf-8")
    machine = run_dir / ".info" / "compare_result.json"
    machine.parent.mkdir(parents=True, exist_ok=True)
    machine.write_text("{}", encoding="utf-8")

    manifest_path = write_manifest(
        wf_id="wf-reports-001",
        workflow_id="demo_workflow",
        asset_name="mihouwang",
        project=project,
        status="SUCCESS",
        elapsed_min=0.1,
        inputs={},
        outputs={"compare.output_path": str(machine)},
        reports=[str(report), str(machine)],
        segments=[],
    )
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert data["reports"] == [str(report)], data["reports"]
    assert data["outputs"]["compare.output_path"] == str(machine)
    print(f"✓ test_manifest_reports_exclude_machine_outputs → {manifest_path}")


def test_root_machine_duplicates_removed_only_when_info_matches():
    project = "_test_run_archive_cleanup"
    _clean_project(project)
    run_dir = create_run_dir("wf-cleanup-001", project, "mihouwang", 1_714_000_200)
    info_dir = run_dir / ".info"
    info_dir.mkdir(parents=True, exist_ok=True)

    duplicate_root = run_dir / "same_compare_result.json"
    duplicate_info = info_dir / duplicate_root.name
    duplicate_root.write_text('{"same": true}', encoding="utf-8")
    duplicate_info.write_text('{"same": true}', encoding="utf-8")

    unique_root = run_dir / "root_only.json"
    unique_root.write_text('{"keep": true}', encoding="utf-8")
    manifest = run_dir / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    removed = cleanup_root_machine_duplicates(run_dir)
    assert str(duplicate_root) in removed, removed
    assert not duplicate_root.exists()
    assert duplicate_info.exists()
    assert unique_root.exists()
    assert manifest.exists()
    print(f"✓ test_root_machine_duplicates_removed_only_when_info_matches → {run_dir}")


if __name__ == "__main__":
    print("=== run_archive unit tests ===\n")
    test_readable_sandbox_name_and_report_path()
    test_same_second_collision_suffix()
    test_manifest_reports_exclude_machine_outputs()
    test_root_machine_duplicates_removed_only_when_info_matches()
    print("\n✅ all pass")
