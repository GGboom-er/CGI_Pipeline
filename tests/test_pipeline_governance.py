"""Regression coverage for the generated CGI governance checker."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_governance_accepts_shared_cores_and_seven_facades():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_pipeline_governance.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "exposed_tools=7" in completed.stdout
    assert "RESULT: PASS" in completed.stdout
