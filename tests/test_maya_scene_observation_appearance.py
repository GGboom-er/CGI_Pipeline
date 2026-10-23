"""Unsupported appearance keeps its geometry and never reveals a false opaque hit."""

from __future__ import annotations

import copy
import types
from collections import Counter

import pytest

from cgi_pipeline.capabilities.maya import scene_geometry as geometry
from cgi_pipeline.capabilities.maya import scene_observation as observation
from cgi_pipeline.capabilities.maya import scene_observation_view as view
from test_maya_scene_observation import files, install_om


class MaterialCmds:
    def __init__(self, shader_type="lambert", transparency=0.0, incoming=None):
        self.shader_type = shader_type
        self.values = {"transparency": [(transparency,) * 3], "refractions": False}
        self.incoming = dict(incoming or {})

    def nodeType(self, name):
        return self.shader_type

    def ls(self, name, **kwargs):
        return ["uuid:" + name]

    def attributeQuery(self, name, **kwargs):
        return name in self.values

    def getAttr(self, plug):
        return self.values[plug.rsplit(".", 1)[1]]

    def listConnections(self, plug, **kwargs):
        if plug.endswith(".surfaceShader"):
            return ["shader.outColor"] if kwargs.get("plugs") else ["shader"]
        return self.incoming.get(plug.rsplit(".", 1)[1], [])


def test_transparent_shader_is_preserved_as_unsupported_state_not_global_exception(monkeypatch):
    install_om(monkeypatch, MFnDependencyNode=lambda node: types.SimpleNamespace(name=lambda: "sg"))
    fn = types.SimpleNamespace(getConnectedShaders=lambda _: ([object()], [0, 0]))
    result = geometry._materials(MaterialCmds("phong", 0.9), fn, 0)
    assert result["pixel_lookup_supported"] is False
    assert result["unsupported_reasons"]
    assert result["groups"][0]["shaders"][0]["attributes"]["transparency"]["value"] == [[0.9] * 3]


@pytest.mark.parametrize("shader_type,attributes,incoming", [
    ("aiStandardSurface", {}, {}), ("lambert", {}, {"displacementShader": ["disp.outDisplacement"]}),
    ("phong", {"refractions": True}, {}), ("blinn", {"transmission": 0.5}, {}),
    ("surfaceShader", {"outTransparency": [(0.0, 0.3, 0.0)]}, {}),
    ("lambert", {}, {"transparency": ["alpha.outAlpha"]})])
def test_unknown_special_and_opacity_shader_state_is_unsupported(monkeypatch, shader_type, attributes, incoming):
    install_om(monkeypatch, MFnDependencyNode=lambda node: types.SimpleNamespace(name=lambda: "sg"))
    cmds = MaterialCmds(shader_type, incoming=incoming)
    cmds.values.update(attributes)
    fn = types.SimpleNamespace(getConnectedShaders=lambda _: ([object()], [0]))
    assert geometry._materials(cmds, fn, 0)["pixel_lookup_supported"] is False


def _pixel_case(monkeypatch, supported, nearest_path="|hair|shape"):
    state = {"view": {"panel": "p"}, "targets": [{"path": "|hair", "mesh_paths": ["|hair|shape"]}],
             "meshes": [{"path": "|hair|shape"}, {"path": "|transparent|shape"}]}
    record = {"state": state, "image": {"width": 10, "height": 10}}
    monkeypatch.setattr(observation, "validate", lambda *args: record)
    monkeypatch.setattr(view, "native_view", lambda *args: None)
    monkeypatch.setattr(view, "clipped_ray", lambda *args: (None, None, 100))
    hit = {"path": nearest_path, "uuid": "same-geometric-hit", "pixel_lookup_supported": supported,
           "pixel_lookup_unsupported_reasons": [] if supported else ["transparent"]}
    monkeypatch.setattr(geometry, "nearest_hit", lambda meshes, *args: hit)
    return observation._pick(object(), {"observation": {}, "pixels": [[5, 5]], "expected_target": "|hair"})


def test_nearest_transparent_geometry_does_not_claim_or_search_opaque_behind(monkeypatch):
    row = _pixel_case(monkeypatch, False, "|transparent|shape")["items"][0]
    assert row["status"] == "UNSUPPORTED_PIXEL_APPEARANCE"
    assert row["nearest_hit"] is None
    assert row["geometry_candidate"]["path"] == "|transparent|shape"
    assert row["expected_target_match"] is None


def test_nearest_opaque_geometry_can_match_with_non_target_transparency_behind_or_elsewhere(monkeypatch):
    row = _pixel_case(monkeypatch, True)["items"][0]
    assert row["status"] == "NEAREST_POLYGON_GEOMETRY"
    assert row["nearest_hit"]["path"] == "|hair|shape"
    assert row["expected_target_match"] is True


def test_target_appearance_is_aggregate_of_its_meshes_only():
    meshes = [{"path": "|hair|shape", "world_bounds": [0, 0, 0, 1, 1, 1],
               "appearance": {"pixel_lookup_supported": True, "unsupported_reasons": []}},
              {"path": "|eye|vitreous", "world_bounds": [0, 0, 0, 1, 1, 1],
               "appearance": {"pixel_lookup_supported": False, "unsupported_reasons": ["transparent"]}}]
    identity = lambda cmds, path: {"path": path, "node_type": "transform", "uuid": "uuid:" + path}
    records = geometry.target_records(None, ["|hair", "|eye"], meshes, identity, None, lambda *args: {})
    assert records[0]["pixel_lookup_supported"] is True
    assert records[1]["pixel_lookup_supported"] is False
    assert records[1]["pixel_lookup_unsupported_reasons"]


def test_opacity_flip_changes_recorded_full_candidate_state_and_rejects_old_image(files, monkeypatch):
    image, state = files
    install_om(monkeypatch, MFnDependencyNode=lambda node: types.SimpleNamespace(name=lambda: "sg"))
    cmds = MaterialCmds()
    fn = types.SimpleNamespace(getConnectedShaders=lambda _: ([object()], [0]))
    state["meshes"][0]["shading_assignment"] = geometry._materials(cmds, fn, 0)
    reference = observation.publish(state, image, 2, 2)
    cmds.values["transparency"] = [(0.5, 0.5, 0.5)]
    current = copy.deepcopy(state)
    current["meshes"][0]["shading_assignment"] = geometry._materials(cmds, fn, 0)
    monkeypatch.setattr(observation, "snapshot", lambda *args: current)
    with pytest.raises(RuntimeError, match="Stale polygon observation.*meshes"):
        observation.validate(cmds, reference)


@pytest.mark.parametrize("distances,expected_status,expected_path,match", [
    ({"|hair|shape": 5, "|transparent|shape": 2}, "UNSUPPORTED_PIXEL_APPEARANCE", "|transparent|shape", None),
    ({"|hair|shape": 2, "|transparent|shape": 5}, "NEAREST_POLYGON_GEOMETRY", "|hair|shape", True),
    ({"|hair|shape": 2, "|transparent|shape": None}, "NEAREST_POLYGON_GEOMETRY", "|hair|shape", True)])
def test_actual_nearest_geometry_does_not_filter_appearance_candidates(
        monkeypatch, distances, expected_status, expected_path, match):
    seen = []
    point = types.SimpleNamespace(x=0.0, y=0.0, z=0.0)
    def function_set(path):
        def intersection(*args):
            seen.append(path)
            distance = distances[path]
            return None if distance is None else (point, distance, 0, 0, 0.2, 0.3)
        return types.SimpleNamespace(closestIntersection=intersection)
    install_om(monkeypatch, MFnMesh=function_set, MFloatPoint=lambda *args: None,
               MFloatVector=lambda *args: None, MSpace=types.SimpleNamespace(kWorld=4))
    monkeypatch.setattr(geometry, "dag_path", lambda path: path)
    meshes = [{"path": path, "uuid": "uuid:" + path, "instance_number": 0,
               "transform": {"path": path.rsplit("|", 1)[0], "uuid": "transform:" + path},
               "appearance": {"pixel_lookup_supported": "transparent" not in path,
                              "unsupported_reasons": ["transparent"] if "transparent" in path else []}}
              for path in distances]
    hit = geometry.nearest_hit(meshes, point, point, 100.0)
    assert seen == list(distances)  # Even unsupported geometry participates in nearest intersection.
    row = observation._pixel_result(4, 5, 4, hit, {"mesh_paths": ["|hair|shape"]})
    candidate = row["geometry_candidate"] or row["nearest_hit"]
    assert (row["status"], candidate["path"], row["expected_target_match"]) == (
        expected_status, expected_path, match)


@pytest.mark.parametrize("shader_type", ["lambert", "phong", "phongE", "blinn", "surfaceShader"])
def test_only_known_native_opaque_base_shaders_are_supported(monkeypatch, shader_type):
    install_om(monkeypatch, MFnDependencyNode=lambda node: types.SimpleNamespace(name=lambda: "sg"))
    cmds = MaterialCmds(shader_type)
    if shader_type == "surfaceShader":
        cmds.values["outTransparency"] = [(0.0, 0.0, 0.0)]
    fn = types.SimpleNamespace(getConnectedShaders=lambda _: ([object()], [0]))
    state = geometry._materials(cmds, fn, 0)
    assert state["pixel_lookup_supported"] is True
    assert state["unsupported_reasons"] == []


def test_shared_materials_are_read_once_per_snapshot_without_merging_instance_assignments(monkeypatch):
    class CountingCmds(MaterialCmds):
        def __init__(self):
            super().__init__()
            self.reads = Counter()
        def attributeQuery(self, name, **kwargs):
            return kwargs["node"] == "shader" and super().attributeQuery(name, **kwargs)
        def getAttr(self, plug):
            self.reads[plug] += 1
            return super().getAttr(plug)
        def listConnections(self, plug, **kwargs):
            if kwargs.get("type") == "displayLayer":
                return []
            if plug.endswith(".surfaceShader"):
                self.reads[(plug, bool(kwargs.get("plugs")))] += 1
            return super().listConnections(plug, **kwargs)
    assignments, connected_calls = {0: [0, 1], 1: [1, 1]}, []
    def function_set(dag):
        def connected(instance):
            connected_calls.append(instance)
            return (["sgA", "sgB"] if instance == 0 else ["sgB", "sgA"], assignments[instance])
        return types.SimpleNamespace(numVertices=3, numPolygons=2, getConnectedShaders=connected,
            getPoints=lambda _: [types.SimpleNamespace(x=float(i), y=0.0, z=0.0) for i in range(3)],
            getVertices=lambda: ([3, 3], [0, 1, 2, 0, 1, 2]),
            getTriangles=lambda: ([1, 1], [0, 1, 2, 0, 1, 2]))
    install_om(monkeypatch, MFnMesh=function_set,
        MFnDependencyNode=lambda group: types.SimpleNamespace(name=lambda: group),
        MSpace=types.SimpleNamespace(kWorld=4))
    paths = [("|first|shape", types.SimpleNamespace(instanceNumber=lambda: 0, inclusiveMatrix=lambda: [])),
             ("|second|shape", types.SimpleNamespace(instanceNumber=lambda: 1, inclusiveMatrix=lambda: []))]
    monkeypatch.setattr(geometry, "_candidate_paths", lambda *args: (paths, set()))
    identity = lambda cmds, path: {"path": path, "uuid": "shared" if path.endswith("|shape") else path}
    cmds = CountingCmds()
    before, _ = geometry.collect(cmds, {}, identity, 1000, 250000)
    assert connected_calls == [0, 1]
    assert cmds.reads["shader.transparency"] == 1
    assert all(cmds.reads[(group + ".surfaceShader", plugs)] == 1
               for group in ("sgA", "sgB") for plugs in (False, True))
    assert [group["shading_group"] for group in before[0]["shading_assignment"]["groups"]] == ["sgA", "sgB"]
    assert [group["shading_group"] for group in before[1]["shading_assignment"]["groups"]] == ["sgB", "sgA"]
    assert before[0]["shading_assignment"]["face_assignments_sha256"] != before[1]["shading_assignment"]["face_assignments_sha256"]
    cmds.values["transparency"] = [(0.5, 0.5, 0.5)]
    after, counts = geometry.collect(cmds, {}, identity, 1000, 250000)
    assert connected_calls == [0, 1, 0, 1]
    assert cmds.reads["shader.transparency"] == 2
    assert all(record["appearance"]["pixel_lookup_supported"] for record in before)
    assert counts["unsupported_mesh_count"] == 2 and before != after
