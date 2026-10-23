import json
import zipfile
from pathlib import Path

import pytest
import yaml

from cgi_pipeline.capabilities.pipeline.pipeline.package_asset import package_asset as operation
from cgi_pipeline.contracts import validate_spec


def _inputs(root, asset="one", textures=True):
    scene = root / (asset + ".ma")
    scene.write_text("// saved Maya scene", encoding="utf-8")
    image = root / "sourceimages" / (asset + ".png")
    image.parent.mkdir(exist_ok=True)
    image.write_bytes(b"corresponding texture")
    unrelated = image.parent / "unrelated.png"
    unrelated.write_bytes(b"not part of this asset")
    data = {"schema_version": 3, "materials": {"material": {
        "color": {"type": "texture", "path": str(image)} if textures else {"type": "solid", "value": [1, 1, 1]},
        "alpha": {"type": "value", "value": 1.0},
        "faces_by_mesh": {"cache|mesh": [0]},
    }}}
    materials = root / (asset + ".json")
    materials.write_text(json.dumps(data), encoding="utf-8")
    return {"run_dir": str(root), "asset_name": asset, "parameters": {
        "scene_path": str(scene), "materials_path": str(materials), "output_path": str(root / (asset + ".zip"))}}


def test_archive_contains_only_one_maya_and_its_referenced_texture(tmp_path):
    payload = _inputs(tmp_path)
    result = operation.execute(payload)
    assert result["status"] == "SUCCESS", result
    with zipfile.ZipFile(result["output"]["output_path"]) as archive:
        assert sorted(archive.namelist()) == ["one/one.ma", "one/sourceimages/one.png"]
        assert archive.read("one/sourceimages/one.png") == b"corresponding texture"
    assert result["output"]["result"]["maya_count"] == 1
    assert result["output"]["result"]["texture_count"] == 1


def test_pure_color_asset_contains_only_maya(tmp_path):
    result = operation.execute(_inputs(tmp_path, textures=False))
    assert result["status"] == "SUCCESS"
    with zipfile.ZipFile(result["output"]["output_path"]) as archive:
        assert archive.namelist() == ["one/one.ma"]


def test_two_roles_are_independent(tmp_path):
    for role in ("one", "two"):
        result = operation.execute(_inputs(tmp_path, role))
        assert result["status"] == "SUCCESS"
        assert all(name.startswith(role + "/") for name in result["output"]["result"]["members"])


def test_missing_texture_fails_before_archive_creation(tmp_path):
    payload = _inputs(tmp_path)
    material_path = Path(payload["parameters"]["materials_path"])
    data = json.loads(material_path.read_text())
    data["materials"]["material"]["color"]["path"] = str(tmp_path / "missing.png")
    material_path.write_text(json.dumps(data), encoding="utf-8")
    assert operation.execute(payload)["status"] == "ERROR"
    assert not Path(payload["parameters"]["output_path"]).exists()


def test_existing_archive_is_never_overwritten(tmp_path):
    payload = _inputs(tmp_path)
    target = Path(payload["parameters"]["output_path"])
    target.write_bytes(b"existing")
    assert operation.execute(payload)["status"] == "ERROR"
    assert target.read_bytes() == b"existing"


@pytest.mark.parametrize("field", ["scene_path", "materials_path", "output_path"])
def test_workspace_escape_is_blocked(tmp_path, field):
    payload = _inputs(tmp_path)
    payload["parameters"][field] = str(tmp_path.parent / "outside.zip")
    assert operation.execute(payload)["status"] == "BLOCKED"


def test_asset_name_cannot_create_archive_traversal(tmp_path):
    payload = _inputs(tmp_path)
    payload["asset_name"] = "../other"
    assert operation.execute(payload)["status"] == "ERROR"


def test_manifest_has_one_canonical_handler():
    path = Path(operation.__file__).with_name("capability.yaml")
    spec = validate_spec(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert spec["api_id"] == operation.API_ID
