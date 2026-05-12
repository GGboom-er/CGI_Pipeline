# -*- coding: utf-8 -*-
# tests/test_run_archive.py
# 验证任务沙盒命名与同一 task 的沙盒稳定映射。

import shutil
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.run_archive import create_run_dir, get_run_report_path


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


if __name__ == "__main__":
    print("=== run_archive unit tests ===\n")
    test_readable_sandbox_name_and_report_path()
    test_same_second_collision_suffix()
    print("\n✅ all pass")
