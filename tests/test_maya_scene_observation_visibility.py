"""Native invisible LOD membership is distinct from unsupported visible drawing."""

from __future__ import annotations

import copy
import hashlib
import math
import struct
import types

import pytest

from cgi_pipeline.capabilities.maya import scene_geometry as geometry
from cgi_pipeline.capabilities.maya import scene_observation as observation
from cgi_pipeline.contracts import ApiContext
from test_maya_scene_observation import DrawingCmds, files  # Shared managed PNG fixture.


@pytest.fixture
def lod_scene(monkeypatch):
    cmds = DrawingCmds()
    cmds.values["|root|lodA"] = {"lodVisibility": False}
    cmds.ls = lambda **_: ["|root|lodA|shape", "|root|visible|shape"]
    cmds.nodeType = lambda _: "mesh"
    def native_path(path):
        return types.SimpleNamespace(isVisible=lambda: (
            "|lodA|" not in path or cmds.values["|root|lodA"]["lodVisibility"]))
    monkeypatch.setattr(geometry, "dag_path", native_path)
    monkeypatch.setattr(geometry, "mesh_record", lambda *args: {"path": args[1], "vertex_count": 8})
    return cmds


def test_native_hidden_lod_branch_is_skipped_before_drawing_checks(lod_scene):
    records, counts = geometry.collect(lod_scene, {}, None, 1000, 250000)
    assert [r["path"] for r in records] == ["|root|visible|shape"]
    assert counts["mesh_instances"] == 1


def test_hidden_to_visible_candidate_membership_invalidates_old_observation(files, lod_scene, monkeypatch):
    image, state = files
    state["meshes"], _ = geometry.collect(lod_scene, {}, None, 1000, 250000)
    reference = observation.publish(state, image, 2, 2)
    lod_scene.values["|root|lodA"]["lodVisibility"] = True
    current = copy.deepcopy(state)
    current["meshes"], _ = geometry.collect(lod_scene, {}, None, 1000, 250000)
    assert len(current["meshes"]) == 2
    monkeypatch.setattr(observation, "snapshot", lambda *args: current)
    with pytest.raises(RuntimeError, match="Stale polygon observation.*meshes"):
        observation.validate(lod_scene, reference)


@pytest.mark.parametrize("attributes", [{"overrideVisibility": False}, {"overrideDisplayType": 1},
                                         {"overrideLevelOfDetail": 1}, {"overrideShading": False}])
def test_native_visible_unsupported_occluder_still_rejects(lod_scene, attributes):
    lod_scene.values["|root|lodA"] = {"lodVisibility": True, "overrideEnabled": True} | attributes
    with pytest.raises(ValueError):
        geometry.collect(lod_scene, {}, None, 1000, 250000)


def _original_hash(values, format_code):
    digest = hashlib.sha256()
    for value in values:
        if not math.isfinite(float(value)):
            raise ValueError("Nonfinite evaluated geometry is unsupported")
        digest.update(struct.pack("<" + format_code, value))
    return digest.hexdigest()


@pytest.mark.parametrize("format_code", ["d", "q"])
@pytest.mark.parametrize("length", [0, 1, 8191, 8192, 8193, 16385])
def test_chunked_numeric_hash_is_byte_identical_to_original(format_code, length):
    edge = [-2**63, 2**63 - 1, 0, 1, -1] if format_code == "q" else [
        -0.0, 0.0, -1.25, 1e-300, 1e300, True, False]
    values = [edge[i % len(edge)] for i in range(length)]
    assert geometry._hash_numbers(iter(values), format_code) == _original_hash(values, format_code)


@pytest.mark.parametrize("values,format_code", [
    ([math.nan], "d"), ([math.inf], "d"), ([-math.inf], "q"),
    ([2**63], "q"), ([-2**63 - 1], "q"), ([1.5], "q"), (["1.25"], "d"),
    ([2**63, math.nan], "q"), ([math.nan, 2**63], "q"),
    ([0] * 8192 + [2**63, math.inf], "q"),
])
def test_numeric_hash_preserves_original_first_error(values, format_code):
    with pytest.raises(Exception) as original:
        _original_hash(values, format_code)
    with pytest.raises(type(original.value)) as current:
        geometry._hash_numbers(iter(values), format_code)
    assert str(current.value) == str(original.value)


@pytest.mark.parametrize("first", [0, 2**63])
def test_numeric_hash_preserves_pack_error_before_later_iterator_error(first):
    def values():
        yield first
        raise RuntimeError("evaluated geometry iterator failed")
    with pytest.raises(Exception) as original:
        _original_hash(values(), "q")
    with pytest.raises(type(original.value)) as current:
        geometry._hash_numbers(values(), "q")
    assert str(current.value) == str(original.value)


def test_numeric_hash_bounds_native_pack_calls_per_chunk(monkeypatch):
    values = list(range(16385))
    expected = _original_hash(values, "q")
    original_pack, sizes = struct.pack, []
    def pack(format_string, *chunk):
        sizes.append(len(chunk))
        return original_pack(format_string, *chunk)
    monkeypatch.setattr(geometry.struct, "pack", pack)
    assert geometry._hash_numbers(iter(values), "q") == expected
    assert sizes == [8192, 8192, 1]


@pytest.fixture
def native_case(monkeypatch):
    from cgi_pipeline.capabilities.maya.asset.maya_capture_viewport import maya_capture_viewport as capture
    state = {"scene": "fixture.ma", "frame": 1, "maya_api_version": 20250300,
             "views": [{"panel": "p", "camera": "|camera", "camera_world_matrix": [1.0] * 16}]}
    class Cmds:
        def __init__(self):
            self.selection, self.modified, self.calls, self.expansions = ["|selected"], False, [], []
            self.hits, self.aliases, self.on_hit = {}, {}, None
        def modelPanel(self, panel, **kwargs):
            assert panel == "p" and kwargs == {"q": True, "modelEditor": True}
            return "editor"
        def editor(self, editor, **kwargs):
            assert editor == "editor" and kwargs == {"q": True, "control": True}
            return "control"
        def hitTest(self, control, x, y):
            assert control == "control"
            self.calls.append((x, y))
            if self.on_hit:
                self.on_hit()
            return self.hits.get((x, y), [])
        def ls(self, name=None, **kwargs):
            if kwargs.get("selection"):
                return list(self.selection)
            self.expansions.append(name)
            assert not name.startswith("|")  # Exact native instance paths must not expand.
            return self.aliases.get(name, [])
        def file(self, **kwargs):
            assert kwargs == {"q": True, "modified": True}
            return self.modified
    cmds = Cmds()
    view = types.SimpleNamespace(portWidth=lambda: 10, portHeight=lambda: 8,
        modelViewMatrix=lambda: [1.0] * 16, projectionMatrix=lambda: list(state.get("projection", [2.0] * 16)))
    def identity(cmds, path):
        if not path.startswith("|") or path == "|gone":
            raise ValueError("unresolved native object")
        return {"path": path, "uuid": "shared" if path.endswith("|shape") else path,
                "instance_number": int("|B|" in path), "node_type": "mesh"}
    monkeypatch.setattr(capture, "_observation", lambda *args: copy.deepcopy({
        key: state[key] for key in ("scene", "frame", "maya_api_version", "views")}))
    monkeypatch.setattr(observation.observation_view, "native_view", lambda *args: view)
    monkeypatch.setattr(observation, "session_identity", lambda *args: {"scene_epoch": state.get("epoch", 0)})
    monkeypatch.setattr(observation, "node_identity", identity)
    def no_scan(*args):
        raise AssertionError("native mode must not scan or validate a polygon snapshot")
    monkeypatch.setattr(geometry, "collect", no_scan)
    monkeypatch.setattr(observation, "snapshot", no_scan)
    monkeypatch.setattr(observation, "validate", no_scan)
    context = ApiContext(executor="maya", extras={"cmds_module": cmds})
    def run(**params):
        return observation.pick_pixels({"mode": "native", "panel": "p", "pixels": [[2, 1]], **params}, context)
    expected = {**copy.deepcopy(state), "scope": "viewport", "width": 10, "height": 8}
    return types.SimpleNamespace(cmds=cmds, state=state, run=run, expected=expected)


def test_native_pick_uses_top_left_hits_without_snapshot_or_ui_writes(native_case):
    case = native_case
    case.cmds.hits[(2, 1)] = ["|A|shape"]
    result = case.run(pixels=[[2, 1], [4, 6]], expected_view=case.expected, expected_target="|A")
    assert result["status"] == "SUCCESS", result
    assert case.cmds.calls == [(2, 1), (4, 6)]
    first, background = result["output"]["items"]
    assert first["candidates"][0]["path"] == "|A|shape"
    assert first["expected_target_match"] is True
    assert background["candidates"] == [] and background["expected_target_match"] is False
    assert case.cmds.selection == ["|selected"] and not case.cmds.modified
    assert case.cmds.expansions == []


@pytest.mark.parametrize("change", ["frame", "views", "projection", "epoch", "selection", "modified"])
def test_native_pick_rejects_view_session_or_selection_change_during_batch(native_case, change):
    case = native_case
    def mutate():
        if change == "selection":
            case.cmds.selection = ["|other"]
        elif change == "modified":
            case.cmds.modified = True
        else:
            case.state[change] = {"frame": 2, "views": [], "projection": [3.0] * 16, "epoch": 1}[change]
    case.cmds.on_hit = mutate
    result = case.run()
    assert result["status"] == "ERROR" and "changed" in result["error"].lower()


@pytest.mark.parametrize("field,value", [("frame", 99), ("width", 11), ("scope", "window")])
def test_native_pick_rejects_mismatching_optional_capture_view_before_hit(native_case, field, value):
    expected = native_case.expected | {field: value}
    result = native_case.run(expected_view=expected)
    assert result["status"] == "ERROR" and native_case.cmds.calls == []


@pytest.mark.parametrize("params", [
    {"pixels": []}, {"pixels": [[1, 1]] * 21}, {"pixels": [[True, 1]]},
    {"pixels": [[10, 1]]}, {"pixels": [[1, 8]]}, {"pixels": [[1.5, 1]]},
    {"panel": ""}, {"expected_target": "shortName"}, {"mode": "guess"},
])
def test_native_pick_rejects_invalid_request_before_hit(native_case, params):
    result = native_case.run(**params)
    assert result["status"] == "ERROR" and native_case.cmds.calls == []
