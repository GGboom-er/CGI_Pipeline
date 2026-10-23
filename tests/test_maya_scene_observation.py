"""Failures that must prevent image/geometry correspondence claims and unsafe reuse."""

from __future__ import annotations

import copy
import hashlib
import json
import struct
import sys
import threading
import types
import zlib

import pytest
import yaml

from cgi_pipeline.capabilities.maya import scene_geometry as geometry
from cgi_pipeline.capabilities.maya import scene_observation as observation
from cgi_pipeline.capabilities.maya import scene_observation_io as io
from cgi_pipeline.capabilities.maya import scene_observation_view as view
from cgi_pipeline.contracts import ApiContext, validate_spec


def install_om(monkeypatch, **values):
    maya, api, om = (types.ModuleType(name) for name in ("maya", "maya.api", "maya.api.OpenMaya"))
    maya.api, api.OpenMaya = api, om
    for key, value in values.items():
        setattr(om, key, value)
    for name, module in (("maya", maya), ("maya.api", api), ("maya.api.OpenMaya", om)):
        monkeypatch.setitem(sys.modules, name, module)
    return om


def png(path, width=2, height=2):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00\xff" * width) * height))
    path.write_bytes(data + chunk(b"IEND", b""))


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(io, "REPOSITORY_ROOT", tmp_path)
    root = tmp_path / "projects" / "task" / "outputs"
    root.mkdir(parents=True)
    image = root / "capture.png"
    png(image)
    state = {"schema_version": 1, "session": {"scene_epoch": 0},
             "view": {"panel": "modelPanel4", "port_size": [2, 2], "viewport": [0, 0, 2, 2],
                      "scope": "viewport", "fit": "full_port_no_crop"},
             "targets": [{"path": "|hair", "uuid": "hair-uuid"}],
             "meshes": [{"path": "|hair|shape", "points_world_sha256": "first"}],
             "limits": {"max_meshes": 1000, "max_vertices": 250000}}
    return image, state


def test_manifest_has_read_and_ephemeral_host_effects():
    from pathlib import Path
    path = Path(observation.__file__).parent / "scene" / "viewport_pick" / "capability.yaml"
    spec = validate_spec(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert spec["access"] == "read"
    assert set(spec["effects"]) == {"scene_read", "file_read", "host_state_write"}
    assert spec["execution_modes"] == ["local", "foreground"]


def test_publish_reference_and_original_byte_identity(files):
    image, state = files
    reference = observation.publish(state, image, 2, 2)
    record = io.read(reference)
    assert record["state"] == state
    assert record["image"]["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert io.managed_path(reference["path"]).read_bytes() == io.canonical(record)
    with pytest.raises(FileExistsError):
        observation.publish(state, image, 2, 2)


@pytest.mark.parametrize("width,height", [(4, 2), (2, 4), (1, 1)])
def test_scaled_or_cropped_image_mapping_is_rejected(files, width, height):
    image, state = files
    with pytest.raises(ValueError, match="dimensions"):
        observation.publish(state, image, width, height)


@pytest.mark.parametrize("key,value", [("scope", "window"), ("fit", "unknown"), ("viewport", [1, 0, 2, 2])])
def test_window_unknown_crop_or_offset_port_cannot_be_published(files, key, value):
    image, state = files
    state["view"][key] = value
    with pytest.raises(ValueError):
        observation.publish(state, image, 2, 2)


def test_sidecar_and_png_tampering_are_independently_rejected(files):
    image, state = files
    reference = observation.publish(state, image, 2, 2)
    path = io.managed_path(reference["path"])
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(RuntimeError, match="sidecar SHA"):
        io.read(reference)
    path.write_bytes(original)
    png(image, 3, 2)
    with pytest.raises(RuntimeError, match="PNG bytes"):
        io.read(reference)


def test_outside_managed_roots_and_png_crc_rejected(files, tmp_path):
    image, _ = files
    outside = tmp_path / "outside.png"
    png(outside)
    with pytest.raises(ValueError, match="managed"):
        io.png_identity(outside)
    data = bytearray(image.read_bytes())
    data[41] ^= 1
    image.write_bytes(data)
    with pytest.raises(ValueError, match="CRC"):
        io.png_identity(image)


@pytest.mark.parametrize("section", ["session", "view", "targets", "meshes"])
def test_old_scene_view_identity_and_same_count_deformation_rejected(files, monkeypatch, section):
    image, state = files
    reference = observation.publish(state, image, 2, 2)
    current = copy.deepcopy(state)
    if section == "session":
        current[section]["scene_epoch"] += 1
    elif section == "view":
        current[section]["focal_length"] = 100
    elif section == "targets":
        current[section][0]["uuid"] = "rebuilt"
    else:
        current[section][0]["points_world_sha256"] = "changed-but-same-count"
    monkeypatch.setattr(observation, "snapshot", lambda *a: current)
    with pytest.raises(RuntimeError, match="Stale polygon observation"):
        observation.validate(object(), reference)


def test_validated_record_is_reused_without_republishing(files, monkeypatch):
    image, state = files
    reference = observation.publish(state, image, 2, 2)
    calls = []
    monkeypatch.setattr(observation, "snapshot", lambda *a: calls.append(a) or copy.deepcopy(state))
    assert observation.validate(object(), reference)["state"] == state
    assert calls[0][1:] == ("modelPanel4", ["|hair"], 1000, 250000)


@pytest.mark.parametrize("pixels", [[], [[-1, 0]], [[2, 0]], [[0, 2]], [[True, 0]], [[0.0, 0]],
                                   [[float("nan"), 0]], [[float("inf"), 0]], [[0]], [[0, 0]] * 21])
def test_invalid_pixel_queries_never_ray_cast(pixels):
    with pytest.raises(ValueError):
        observation._pixels(pixels, 2, 2)


def test_all_four_original_image_corners_are_valid():
    values = [[0, 0], [1, 0], [0, 1], [1, 1]]
    assert observation._pixels(values, 2, 2) == values


@pytest.mark.parametrize("values", [(0, 20), (2001, 20), (1, 0), (1, 2000001), (True, 20)])
def test_complete_geometry_budget_has_hard_limits(values):
    with pytest.raises(ValueError):
        observation._budgets(*values)


def test_same_vertex_count_different_points_and_topology_hashes(monkeypatch):
    install_om(monkeypatch, MSpace=types.SimpleNamespace(kWorld=4))
    points = [types.SimpleNamespace(x=0.0, y=0.0, z=0.0), types.SimpleNamespace(x=1.0, y=1.0, z=1.0)]
    fn = types.SimpleNamespace(getPoints=lambda _: points)
    before = geometry._points(fn)
    points[1].y = 2.0
    assert geometry._points(fn)[1] != before[1]
    assert geometry._hash_numbers([0, 1, 2], "q") != geometry._hash_numbers([0, 2, 1], "q")


def test_callbacks_are_lazy_unique_and_increment_only_native_epoch(monkeypatch):
    callbacks = []
    scene = types.SimpleNamespace(kAfterNew=1, kAfterOpen=2,
                                  addCallback=lambda event, fn: callbacks.append((event, fn)) or len(callbacks))
    install_om(monkeypatch, MSceneMessage=scene, MMessage=types.SimpleNamespace(removeCallback=lambda _: None))
    monkeypatch.delattr(sys, observation._SESSION_KEY, raising=False)
    monkeypatch.setattr(observation, "_process_created_at", lambda: 123.25)
    cmds = types.SimpleNamespace(about=lambda **_: 20250300)
    first = observation.session_identity(cmds)
    assert first == observation.session_identity(cmds)
    assert len(callbacks) == 2
    callbacks[0][1]()
    second = observation.session_identity(cmds)
    assert second["scene_epoch"] == first["scene_epoch"] + 1
    assert second["session_nonce"] == first["session_nonce"]


def test_partial_callback_install_fails_without_leaking_first(monkeypatch):
    removed = []
    def register(event, fn):
        if event == 2:
            raise RuntimeError("native registration failure")
        return 9
    scene = types.SimpleNamespace(kAfterNew=1, kAfterOpen=2, addCallback=register)
    install_om(monkeypatch, MSceneMessage=scene,
               MMessage=types.SimpleNamespace(removeCallback=lambda value: removed.append(value)))
    monkeypatch.delattr(sys, observation._SESSION_KEY, raising=False)
    monkeypatch.setattr(observation, "_process_created_at", lambda: 123.25)
    with pytest.raises(RuntimeError, match="registration failure"):
        observation.session_identity(types.SimpleNamespace(about=lambda **_: 20250300))
    assert removed == [9]
    assert not hasattr(sys, observation._SESSION_KEY)


def test_nonmain_thread_fails_before_any_maya_import():
    errors = []
    def call():
        try:
            observation.require_main_thread()
        except RuntimeError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=call)
    thread.start()
    thread.join()
    assert errors == ["Maya scene observation requires the Python main thread"]


class PanelCmds:
    def __init__(self):
        self.flags = {key: False for key in view._FALSE_FLAGS}
        self.flags.update({key: "" for key in view._EMPTY_FLAGS})
        self.flags.update({key: False for key in view._DISPLAY_FLAGS})
        self.flags.update(rendererName="vp2Renderer", polymeshes=True, mainListConnection="worldList",
                          displayAppearance="smoothShaded")

    def modelEditor(self, panel, **kwargs):
        return self.flags[next(key for key in kwargs if key != "query")]

    def isolateSelect(self, panel, **kwargs):
        return False

    def selectionConnection(self, name, **kwargs):
        return "worldList" if name in ("worldList", "generatedSelectionConnection42") else "connection"


@pytest.mark.parametrize("flag,value", [("isFiltered", True), ("viewSelected", True), ("objectFilter", "f"),
                                         ("rendererOverrideName", "plugin"), ("sceneRenderFilter", "x"),
                                         ("xray", True),
                                         ("displayAppearance", "wireframe"), ("polymeshes", False)])
def test_panel_state_not_just_dag_visibility_is_required(flag, value):
    cmds = PanelCmds()
    assert view.display_state(cmds, "panel")["rendererName"] == "vp2Renderer"
    cmds.flags[flag] = value
    with pytest.raises(ValueError):
        view.display_state(cmds, "panel")


def test_world_list_identity_uses_native_type_not_generated_name():
    cmds = PanelCmds()
    cmds.flags["mainListConnection"] = "generatedSelectionConnection42"
    assert view.display_state(cmds, "panel")["mainListConnectionType"] == "worldList"


@pytest.mark.parametrize("connection", ["lockedActiveList1", "", None])
def test_native_unfiltered_default_locked_connection_is_provenance(connection):
    cmds = PanelCmds()
    cmds.flags["mainListConnection"] = connection
    assert view.display_state(cmds, "panel")["mainListConnection"] == connection
    cmds.flags["isFiltered"] = True
    with pytest.raises(ValueError, match="filtering"):
        view.display_state(cmds, "panel")


class Point:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    def length(self):
        return (self.x**2 + self.y**2 + self.z**2)**0.5

    def normal(self):
        length = self.length()
        return Point(self.x / length, self.y / length, self.z / length)

    def __mul__(self, value):
        if isinstance(value, list):
            translated = getattr(self, "is_point", False)
            return Point(self.x + (value[12] if translated else 0),
                         self.y + (value[13] if translated else 0),
                         self.z + (value[14] if translated else 0))
        return Point(self.x * value, self.y * value, self.z * value)

    def __add__(self, other):
        return Point(self.x + other.x, self.y + other.y, self.z + other.z)


class Position(Point):
    is_point = True


@pytest.mark.parametrize("direction,expected_start,expected_length", [
    ((0, 0, -1), 29.9, 999.9), ((0.6, 0, -0.8), 29.9, 1249.875)])
def test_native_four_argument_ray_clips_to_camera_planes(monkeypatch, direction, expected_start, expected_length):
    install_om(monkeypatch, MPoint=Position, MVector=Point, MMatrix=list)
    calls = []
    def to_world(x, y, origin, vector):
        calls.append((x, y))
        origin.x, origin.y, origin.z = 0, 0, 30
        vector.x, vector.y, vector.z = direction
    native = types.SimpleNamespace(viewToWorld=to_world)
    matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -30, 1]
    record = {"model_view_matrix_row_major": matrix,
              "camera_params": {"nearClipPlane": 0.1, "farClipPlane": 1000.0}}
    origin, _, maximum = view.clipped_ray(native, 7, 9, record)
    assert calls == [(7, 9)]
    assert origin.z == pytest.approx(expected_start)
    assert maximum == pytest.approx(expected_length)


def test_identity_keeps_same_uuid_instances_and_parent_paths_separate(monkeypatch):
    paths = ["|root|hair|shared", "|root|foot|shared"]
    class Dag:
        def __init__(self, path):
            self.path = path
        def fullPathName(self):
            return self.path
        def instanceNumber(self):
            return paths.index(self.path)
        def isInstanced(self):
            return True
    install_om(monkeypatch, MFnDagNode=lambda dag: types.SimpleNamespace(
        getAllPaths=lambda: [Dag(path) for path in paths]))
    monkeypatch.setattr(geometry, "dag_path", Dag)
    class Cmds:
        def ls(self, path, **kwargs):
            return ["shared-uuid" if path in paths else "uuid:" + path] if kwargs.get("uuid") else [path]
        def nodeType(self, path):
            return "mesh" if path in paths else "transform"
    first, second = [observation.node_identity(Cmds(), p) for p in paths]
    assert first["uuid"] == second["uuid"]
    assert first["path"] != second["path"]
    assert (first["instance_number"], second["instance_number"]) == (0, 1)
    assert first["all_instance_paths"] == sorted(paths)
    assert [p["path"] for p in first["parents"]] == ["|root", "|root|hair"]


def test_collect_cannot_truncate_an_occluder_or_ignore_a_visible_curve(monkeypatch):
    class Cmds:
        def ls(self, **kwargs):
            return ["|hair|shape", "|occluder|shape", "|control|shape"]
        def nodeType(self, path):
            return "nurbsCurve" if "control" in path else "mesh"
        def attributeQuery(self, *args, **kwargs):
            return False
        def listConnections(self, *args, **kwargs):
            return []
    monkeypatch.setattr(geometry, "dag_path", lambda path: types.SimpleNamespace(isVisible=lambda: True))
    monkeypatch.setattr(geometry, "mesh_record", lambda *args: {"path": args[1], "vertex_count": 8})
    cmds = Cmds()
    with pytest.raises(ValueError, match="drawable type"):
        geometry.collect(cmds, {"nurbsCurves": True}, None, 1000, 250000)
    with pytest.raises(ValueError, match="no truncated"):
        geometry.collect(cmds, {"nurbsCurves": False}, None, 1, 250000)
    records, counts = geometry.collect(cmds, {"nurbsCurves": False}, None, 1000, 250000)
    assert len(records) == 2 and counts["vertices"] == 16


def test_pick_flips_original_y_and_reports_occluder_target_mismatch(monkeypatch):
    state = {"view": {"panel": "panel"}, "meshes": [],
             "targets": [{"path": "|hair", "mesh_paths": ["|hair|shape"]}]}
    record = {"state": state, "image": {"width": 10, "height": 10}}
    calls = []
    monkeypatch.setattr(observation, "validate", lambda *a: calls.append("validated") or record)
    monkeypatch.setattr(view, "native_view", lambda *a: None)
    monkeypatch.setattr(view, "clipped_ray", lambda native, x, y, state: calls.append((x, y)) or (None, None, 100))
    monkeypatch.setattr(geometry, "nearest_hit", lambda *a: {"path": "|occluder|shape", "pixel_lookup_supported": True})
    output = observation._pick(object(), {"observation": {}, "pixels": [[7, 3]], "expected_target": "|hair"})
    assert calls == ["validated", (7, 6), "validated"]
    assert output["items"][0]["expected_target_match"] is False
    assert output["items"][0]["nearest_hit"]["path"] == "|occluder|shape"


def test_pick_revalidates_after_native_queries_before_returning(monkeypatch):
    calls = []
    def validate(*_):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("changed geometry during query")
        return {"state": {"view": {"panel": "p"}, "meshes": [], "targets": []},
                "image": {"width": 2, "height": 2}}
    monkeypatch.setattr(observation, "validate", validate)
    monkeypatch.setattr(view, "native_view", lambda *a: None)
    monkeypatch.setattr(view, "clipped_ray", lambda *a: (None, None, 100))
    monkeypatch.setattr(geometry, "nearest_hit", lambda *a: None)
    with pytest.raises(RuntimeError, match="changed geometry"):
        observation._pick(object(), {"observation": {}, "pixels": [[0, 0]]})


def test_receipt_rejects_missing_maya_context():
    result = observation.pick_pixels({"observation": {}, "pixels": [[0, 0]]}, ApiContext(executor="maya"))
    assert result["status"] == "ERROR"
    assert result["error_code"] == "MAYA_OBSERVATION_REJECTED"


@pytest.mark.parametrize("attribute,value,connected", [
    ("transparency", [(0.0, 0.1, 0.0)], False), ("outTransparency", [(0.1, 0.0, 0.0)], False),
    ("opacity", [(1.0, 0.5, 1.0)], False), ("opacity", 0.0, False),
    ("transparency", [(0.0, 0.0, 0.0)], True), ("opacity", [(1.0, 1.0, 1.0)], True)])
def test_known_alpha_and_driven_alpha_are_recorded_as_unsupported(attribute, value, connected):
    cmds = types.SimpleNamespace(attributeQuery=lambda name, **_: name == attribute,
                                 getAttr=lambda _: value,
                                 listConnections=lambda *a, **k: ["texture"] if connected else [])
    result = geometry._opaque_attributes(cmds, "material")
    assert result["unsupported_reasons"]
    assert result["attributes"][attribute]["present"] is True


class DrawingCmds:
    def __init__(self, parent_attributes=None):
        self.values = {"|root": dict(parent_attributes or {})}

    def attributeQuery(self, attribute, node, **kwargs):
        return attribute in self.values.get(node, {})

    def getAttr(self, plug):
        node, attribute = plug.rsplit(".", 1)
        return self.values[node][attribute]

    def listConnections(self, *args, **kwargs):
        return []


@pytest.mark.parametrize("display_type", [0, 2])
def test_native_full_shaded_normal_and_reference_overrides_are_observable(display_type):
    cmds = DrawingCmds({"overrideEnabled": True, "overrideDisplayType": display_type})
    result = geometry._drawing(cmds, "|root|mesh", {})
    assert result["reference_display_nodes"] == (["|root"] if display_type == 2 else [])
    assert result["ui_selection_and_editability_not_certified"] is True
    assert result["nodes"][0]["attributes"]["overrideDisplayType"] == display_type
    assert json.loads(io.canonical(result)) == result  # Maya color tuples cannot cause perpetual stale reads.


@pytest.mark.parametrize("changes", [{"overrideDisplayType": 1}, {"overrideDisplayType": 7},
    {"overrideLevelOfDetail": 1}, {"overrideShading": False}, {"overrideTexturing": False},
    {"overrideVisibility": False}, {"hideOnPlayback": True}, {"template": True},
    {"ghosting": True}, {"lodVisibility": False}, {"overrideColorA": 0.5}])
def test_unsupported_ancestor_drawing_cannot_silently_expose_a_mesh_behind(changes):
    cmds = DrawingCmds({"overrideEnabled": True, "overrideDisplayType": 2} | changes)
    with pytest.raises(ValueError):
        geometry._drawing(cmds, "|root|occluderShape", {})


def test_parent_drawing_values_are_recorded_and_make_old_observation_stale(files, monkeypatch):
    image, state = files
    cmds = DrawingCmds({"overrideEnabled": True, "overrideDisplayType": 2, "overrideColor": 4})
    state["meshes"][0]["drawing"] = geometry._drawing(cmds, "|root|mesh", {})
    reference = observation.publish(state, image, 2, 2)
    current = copy.deepcopy(state)
    cmds.values["|root"]["overrideColor"] = 13
    current["meshes"][0]["drawing"] = geometry._drawing(cmds, "|root|mesh", {})
    assert current["meshes"][0]["drawing"] != state["meshes"][0]["drawing"]
    monkeypatch.setattr(observation, "snapshot", lambda *args: current)
    with pytest.raises(RuntimeError, match="Stale polygon observation.*meshes"):
        observation.validate(cmds, reference)
