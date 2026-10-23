"""The packaging boundary uses real files and the existing copy capability."""

import json
from pathlib import Path

import pytest
import yaml

from cgi_pipeline.contracts import validate_spec
from cgi_pipeline.execution import execute_api
from cgi_pipeline.paths import REPOSITORY_ROOT
from cgi_pipeline.capabilities.pipeline.pipeline.package_textures import package_textures as operation


def _material(color, alpha=None):
    return {"color": color, "alpha": alpha or {"type": "value", "value": 1.0},
            "faces_by_mesh": {"cache|mesh": [0, 1]}, "source": {"tile": 1001}}


def _texture(path):
    return {"type": "texture", "path": str(path), "approximation": "direct"}


def _run(root, materials):
    input_path = root / "materials.json"
    input_path.write_text(json.dumps({"schema_version": 3, "materials": materials}), encoding="utf-8")
    params = {"materials_path": str(input_path), "output_path": str(root / "packed.json"),
              "texture_dir": str(root / "sourceimages")}
    return operation.execute({"run_dir": str(root), "parameters": params}), params


def test_real_xiangc_sample_canonical_adapter_preserves_source(tmp_path):
    manifest = Path(operation.__file__).with_name("capability.yaml")
    spec = validate_spec(yaml.safe_load(manifest.read_text(encoding="utf-8")))
    sample = REPOSITORY_ROOT / "projects/ysj/PL268/.info/ysj_prp_xiangC_tex_texMaster_v001_materials.json"
    original = sample.read_bytes()
    raw = json.loads(original)
    source = Path(raw["materials"]["xiangC_Color.1001"]["color"]["path"])
    source_bytes = source.read_bytes()
    params = {"materials_path": str(sample), "output_path": str(tmp_path / "packed.json"),
              "texture_dir": str(tmp_path / "sourceimages")}
    result = execute_api(spec["api_id"], params, {"run_dir": str(tmp_path)},
                         catalog_snapshot=({spec["api_id"]: spec}, "isolated-test"))
    assert result["status"] == "SUCCESS", result
    packed = json.loads(Path(result["output"]["output_path"]).read_text(encoding="utf-8"))
    destination = Path(packed["materials"]["xiangC_Color.1001"]["color"]["path"])
    assert destination.is_relative_to(tmp_path) and destination.read_bytes() == source_bytes
    packed["materials"]["xiangC_Color.1001"]["color"]["path"] = str(source).replace("\\", "/")
    assert packed == raw and sample.read_bytes() == original and source.read_bytes() == source_bytes
    assert result["output"]["result"]["copied_count"] == 1


def test_color_alpha_udim_references_deduplicate_and_keep_collisions_separate(tmp_path):
    paths = []
    for directory, name, value in (("a", "Color.1001.tif", b"one"), ("b", "Color.1001.tif", b"two"),
                                   ("a", "Alpha.1002.tif", b"alpha")):
        path = tmp_path / directory / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(value)
        paths.append(path)
    materials = {"first": _material(_texture(paths[0]), _texture(paths[0])),
                 "second": _material(_texture(paths[1]), _texture(paths[2]))}
    result, params = _run(tmp_path, materials)
    assert result["status"] == "SUCCESS", result
    mapping = result["output"]["path_mapping"]
    assert len(mapping) == 3 and mapping[str(paths[0])] != mapping[str(paths[1])]
    for source, destination in mapping.items():
        assert Path(destination).name == Path(source).name
        assert Path(destination).read_bytes() == Path(source).read_bytes()
    packed = json.loads(Path(params["output_path"]).read_text(encoding="utf-8"))
    assert packed["materials"]["first"]["color"]["path"] == packed["materials"]["first"]["alpha"]["path"]
    assert result["output"]["result"]["reference_count"] == 4
    assert result["output"]["result"]["copied_count"] == 3
    repeated = operation.execute({"run_dir": str(tmp_path), "parameters": params})
    assert repeated["status"] == "SUCCESS" and repeated["output"]["result"]["reused_count"] == 3


def test_missing_reference_fails_before_any_copy(tmp_path):
    source = tmp_path / "valid.tif"
    source.write_bytes(b"valid")
    result, params = _run(tmp_path, {"ok": _material(_texture(source)),
                                     "missing": _material(_texture(tmp_path / "missing.1002.tif"))})
    assert result["status"] == "ERROR" and "不存在" in result["error"]
    assert not Path(params["output_path"]).exists() and not Path(params["texture_dir"]).exists()


@pytest.mark.parametrize("field", ["output_path", "texture_dir"])
def test_output_escape_is_blocked_before_writes(tmp_path, field):
    result, params = _run(tmp_path, {"solid": _material({"type": "solid", "value": [1, 1, 1]})})
    assert result["status"] == "SUCCESS"
    params[field] = str(tmp_path.parent / "escaped")
    blocked = operation.execute({"run_dir": str(tmp_path), "parameters": params})
    assert blocked["status"] == "BLOCKED" and not Path(params[field]).exists()


@pytest.mark.parametrize("value", [None, {"schema_version": 2, "materials": {}},
                                    {"schema_version": 3, "materials": {"bad": {"color": []}}}])
def test_invalid_schema_does_not_generate_output(tmp_path, value):
    input_path = tmp_path / "bad.json"
    input_path.write_text(json.dumps(value), encoding="utf-8")
    result = operation.execute({"run_dir": str(tmp_path), "parameters": {
        "materials_path": str(input_path), "output_path": str(tmp_path / "packed.json"),
        "texture_dir": str(tmp_path / "sourceimages"),
    }})
    assert result["status"] == "ERROR" and not (tmp_path / "packed.json").exists()


def test_existing_different_file_is_not_overwritten(tmp_path):
    source = tmp_path / "server" / "Color.1001.tif"
    source.parent.mkdir()
    source.write_bytes(b"server")
    target = tmp_path / "sourceimages" / source.name
    target.parent.mkdir()
    target.write_bytes(b"other asset")
    result, params = _run(tmp_path, {"asset": _material(_texture(source))})
    assert result["status"] == "ERROR" and target.read_bytes() == b"other asset"
    assert not Path(params["output_path"]).exists()
