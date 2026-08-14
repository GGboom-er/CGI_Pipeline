from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_maya_module_points_only_to_the_src_package():
    text = (ROOT / "deploy" / "maya" / "modules" / "CGIPipeline.mod").read_text("utf-8")
    assert text.splitlines() == ["+ CGIPipeline 1.0 ../../..", "PYTHONPATH +:=src"]


def test_maya_installer_manages_only_the_module_search_path():
    text = (ROOT / "deploy" / "maya" / "install.ps1").read_text("utf-8")
    assert 'VariableName = "MAYA_MODULE_PATH"' in text
    assert 'SetEnvironmentVariable($VariableName' in text
    assert 'SetEnvironmentVariable("PYTHONPATH"' not in text


def test_maya_doctor_uses_the_real_public_sdk_and_read_only_smoke():
    text = (ROOT / "deploy" / "maya" / "doctor.py").read_text("utf-8")
    assert "from cgi_pipeline import api, tools" in text
    assert '"maya.rig.skin.query_weights"' in text
    assert 'tools._load_entrypoint("adpose", "maya")' in text

