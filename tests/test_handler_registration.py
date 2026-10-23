"""A manifest must not advertise a handler that cannot be called."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("catalog_builder", ROOT / "tools/build_catalogs.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.mark.parametrize("field,value", [("python_paths", []), ("versions", {"blender": "5.0"}),
                                        ("lifecycle", {"ue": "addon"})])
def test_invalid_tool_loading_contract_is_rejected(field, value):
    row = builder.build_packages()[0]
    row[field] = value
    with pytest.raises(builder.ApiContractError):
        builder._validate_packages({"packages": [row]})


def test_handler_function_and_signature_are_checked_without_import(tmp_path):
    path = tmp_path / "cgi_pipeline/capabilities/maya/sample.py"
    path.parent.mkdir(parents=True)
    spec = {"api_id": "maya.sample.run", "executor": "maya",
            "handler": "cgi_pipeline.capabilities.maya.sample:run"}
    path.write_text("raise RuntimeError('must not import')\ndef run(params, context): pass\n", encoding="utf-8")
    builder.validate_handler(spec, tmp_path)
    for text in ("def renamed(params, context): pass\n", "run = 42\n", "def run(params): pass\n"):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(builder.ApiContractError):
            builder.validate_handler(spec, tmp_path)


def test_handler_must_stay_in_executor_owner(tmp_path):
    spec = {"api_id": "maya.sample.run", "executor": "maya", "handler": "cgi_pipeline.capabilities.ue.sample:run"}
    with pytest.raises(builder.ApiContractError):
        builder.validate_handler(spec, tmp_path)


def test_new_manifest_becomes_discoverable_after_generation(tmp_path, monkeypatch):
    from cgi_pipeline import catalog
    original_path = catalog._CATALOG_PATH
    original_version = catalog.catalog_version()
    spec = catalog.get_capability("maya.scene.nodes.find")
    spec["api_id"] = "maya.scene.nodes.registration_probe"
    spec.pop("manifest_path", None)
    manifest_root = tmp_path / "capabilities"
    manifest_root.mkdir()
    (manifest_root / "capability.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    monkeypatch.setattr(builder, "CAPABILITIES_ROOT", manifest_root)
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    generated = tmp_path / "capabilities.json"
    generated.write_text(json.dumps(builder.build_capabilities()), encoding="utf-8")
    try:
        monkeypatch.setattr(catalog, "_CATALOG_PATH", generated)
        catalog.reload()
        rows = catalog.page_capabilities(executor="maya", capability="scene.nodes", operation="find")
        assert [row["api_id"] for row in rows["apis"]] == [spec["api_id"]]
        assert catalog.capability_help(spec["api_id"])["inputs"] == spec["inputs"]
        assert catalog.catalog_version() != original_version
    finally:
        monkeypatch.setattr(catalog, "_CATALOG_PATH", original_path)
        catalog.reload()
