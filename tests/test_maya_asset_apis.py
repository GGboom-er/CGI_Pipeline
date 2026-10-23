"""Contract tests for the four previously-placeholder Maya asset APIs.

The fake ``cmds`` surface is deliberately small: it exercises the adapter
boundary and the observable receipt, without pretending to be a Maya runtime.
"""

from __future__ import annotations

import sys
import types


class FakeCmds:
    def __init__(self):
        self.nodes = {
            "|root": {"type": "transform", "parent": "", "t": [0, 0, 0]},
            "|root|head": {"type": "transform", "parent": "|root", "t": [1, 2, 3]},
            "|root|head|headShape": {"type": "mesh", "parent": "|root|head"},
        }
        self.selection = []
        self.attrs = {"|root.visibility": 1}

    def ls(self, value=None, **kwargs):
        if kwargs.get("selection"):
            return list(self.selection)
        if kwargs.get("uuid"):
            return ["uuid-" + str(value).replace("|", "_")]
        if value is None or value == "*":
            values = list(self.nodes)
        else:
            values = [p for p in self.nodes if p == value or p.rsplit("|", 1)[-1] == value]
        if kwargs.get("type"):
            values = [p for p in values if self.nodes[p]["type"] == kwargs["type"]]
        return values

    def nodeType(self, node): return self.nodes[node]["type"]
    def objExists(self, plug): return plug in self.nodes or plug in self.attrs

    def listRelatives(self, node, **kwargs):
        if kwargs.get("parent"):
            parent = self.nodes[node].get("parent")
            return [parent] if parent else []
        if kwargs.get("shapes"):
            return [p for p, row in self.nodes.items()
                    if row.get("parent") == node and row["type"] in {"mesh", "camera"}
                    and not (kwargs.get("noIntermediate") and
                             self.attrs.get(p + ".intermediateObject", False))]
        return []

    def xform(self, node, **kwargs):
        if kwargs.get("query") or kwargs.get("q"):
            if kwargs.get("translation") or kwargs.get("t"): return list(self.nodes[node].get("t", [0, 0, 0]))
            if kwargs.get("rotation") or kwargs.get("ro"): return [0, 0, 0]
            if kwargs.get("scale") or kwargs.get("s"): return [1, 1, 1]
            if kwargs.get("matrix"): return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    def polyEvaluate(self, node, **kwargs):
        return {"vertex": 8, "edge": 12, "face": 6, "triangle": 12}.get(next(iter(kwargs), ""), 0)
    def polyUVSet(self, node, **kwargs): return ["map1"]
    def exactWorldBoundingBox(self, node): return [0, 0, 0, 1, 1, 1]
    def listConnections(self, *args, **kwargs): return []
    def listHistory(self, node): return []

    def select(self, items=None, **kwargs):
        if kwargs.get("clear"): self.selection = []
        elif kwargs.get("add"): self.selection.extend(x for x in (items or []) if x not in self.selection)
        elif kwargs.get("deselect"): self.selection = [x for x in self.selection if x not in (items or [])]
        else: self.selection = list(items or [])

    def viewFit(self, *args, **kwargs): pass

    def _primitive(self, node_type, name):
        transform = "|" + name
        shape = transform + "|" + name + "Shape"
        self.nodes[transform] = {"type": "transform", "parent": "", "t": [0, 0, 0]}
        self.nodes[shape] = {"type": "mesh", "parent": transform}
        return [transform, shape]
    def polyCube(self, **kwargs): return self._primitive("mesh", kwargs.get("name", "pCube1"))
    polySphere = polyCylinder = polyCone = polyPlane = polyTorus = polyCube

    def parent(self, node, parent):
        name = node.rsplit("|", 1)[-1]
        new = parent + "|" + name
        self.nodes[new] = self.nodes.pop(node)
        self.nodes[new]["parent"] = parent
        for path, row in list(self.nodes.items()):
            if row.get("parent") == node:
                child = path.rsplit("|", 1)[-1]
                self.nodes[parent + "|" + name + "|" + child] = self.nodes.pop(path)
                self.nodes[parent + "|" + name + "|" + child]["parent"] = new
        return [new]

    def getAttr(self, plug, **kwargs):
        if kwargs.get("lock"): return False
        return self.attrs.get(plug, 0)
    def setAttr(self, plug, *values, **kwargs): self.attrs[plug] = values[0] if len(values) == 1 else list(values)


def _install_fake_maya(fake):
    maya = types.ModuleType("maya")
    maya.cmds = fake
    sys.modules["maya"] = maya
    sys.modules["maya.cmds"] = fake


def test_placeholder_maya_asset_apis_are_executable(monkeypatch):
    fake = FakeCmds(); _install_fake_maya(fake)
    from cgi_pipeline.capabilities.maya.asset.maya_create_primitive import maya_create_primitive
    from cgi_pipeline.capabilities.maya.asset.maya_get_object_info import maya_get_object_info
    from cgi_pipeline.capabilities.maya.asset.maya_select_objects import maya_select_objects
    from cgi_pipeline.capabilities.maya.asset.maya_set_attribute import maya_set_attribute

    created = maya_create_primitive.execute({"parameters": {"primitive_type": "cube", "name": "probe"}})
    assert created["status"] == "SUCCESS"
    info = maya_get_object_info.execute({"parameters": {"node": "|root|head"}})
    assert info["status"] == "SUCCESS" and info["output"]["type"] == "transform"
    selected = maya_select_objects.execute({"parameters": {"objects": ["|root|head"], "mode": "replace"}})
    assert selected["status"] == "SUCCESS" and selected["output"]["after"] == ["|root|head"]
    changed = maya_set_attribute.execute({"parameters": {"plug": "|root.visibility", "value": 0}})
    assert changed["status"] == "SUCCESS" and changed["output"]["after"] == 0


def _inspect_object(monkeypatch, fake, node):
    import importlib

    maya = types.ModuleType("maya")
    maya.cmds = fake
    monkeypatch.setitem(sys.modules, "maya", maya)
    monkeypatch.setitem(sys.modules, "maya.cmds", fake)
    module = importlib.import_module(
        "cgi_pipeline.capabilities.maya.asset.maya_get_object_info.maya_get_object_info"
    )
    monkeypatch.setattr(module, "cmds", fake)
    return module.execute({"parameters": {"node": node}})


def test_object_info_transform_chooses_nonintermediate_mesh(monkeypatch):
    fake = FakeCmds()
    shape = "|root|head|headShape"
    original = "|root|head|headShapeOrig"
    fake.nodes = {**{p: row for p, row in fake.nodes.items() if p != shape},
                  original: {"type": "mesh", "parent": "|root|head"},
                  shape: {"type": "mesh", "parent": "|root|head"}}
    fake.attrs[original + ".intermediateObject"] = True
    receipt = _inspect_object(monkeypatch, fake, "|root|head")
    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["shapes"] == [original, shape]
    assert receipt["output"]["mesh"]["shape"] == shape


def test_object_info_direct_mesh_and_explicit_original_inspect_the_requested_shape(monkeypatch):
    fake = FakeCmds()
    original = "|root|head|headShapeOrig"
    fake.nodes[original] = {"type": "mesh", "parent": "|root|head"}
    fake.attrs[original + ".intermediateObject"] = True
    for node in ("|root|head|headShape", original):
        receipt = _inspect_object(monkeypatch, fake, node)
        assert receipt["status"] == "SUCCESS"
        assert receipt["output"]["path"] == node
        assert receipt["output"]["mesh"]["shape"] == node
        assert receipt["output"]["mesh"]["vertices"] == 8


def test_object_info_direct_camera_inspects_its_own_focal_length(monkeypatch):
    fake = FakeCmds()
    camera = "|root|cameraShape"
    fake.nodes[camera] = {"type": "camera", "parent": "|root"}
    fake.attrs[camera + ".focalLength"] = 35.0
    receipt = _inspect_object(monkeypatch, fake, camera)
    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["camera"] == {"shape": camera, "focal_length": 35.0}


def test_object_info_query_failure_is_partial_and_does_not_forge_zero(monkeypatch):
    class BrokenCmds(FakeCmds):
        def polyEvaluate(self, node, **kwargs):
            if kwargs.get("vertex"):
                raise RuntimeError("fixture topology query failure")
            return super().polyEvaluate(node, **kwargs)

        def xform(self, node, **kwargs):
            if kwargs.get("ro"):
                raise RuntimeError("fixture rotation query failure")
            return super().xform(node, **kwargs)

    receipt = _inspect_object(monkeypatch, BrokenCmds(), "|root|head")
    assert receipt["status"] == "PARTIAL"
    assert receipt["output"]["mesh"]["vertices"] is None
    assert receipt["output"]["mesh"]["edges"] == 12
    assert receipt["output"]["transform"]["rotation"] is None
    failures = {row["field"]: row for row in receipt["output"]["query_errors"]}
    assert set(failures) == {"transform.rotation", "mesh.vertices"}
    assert failures["mesh.vertices"]["error_type"] == "RuntimeError"
    assert "fixture topology" in failures["mesh.vertices"]["error"]


def test_object_info_ambiguous_name_is_rejected(monkeypatch):
    fake = FakeCmds()
    fake.nodes["|other|head"] = {"type": "transform", "parent": "|other"}
    receipt = _inspect_object(monkeypatch, fake, "head")
    assert receipt["status"] == "ERROR"
    assert "不唯一" in receipt["error"]


def test_object_info_real_zero_topology_is_success_and_distinct_from_failure(monkeypatch):
    class EmptyMeshCmds(FakeCmds):
        def polyEvaluate(self, node, **kwargs):
            return 0

        def polyUVSet(self, node, **kwargs):
            return None

    receipt = _inspect_object(monkeypatch, EmptyMeshCmds(), "|root|head|headShape")
    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["mesh"]["vertices"] == 0
    assert receipt["output"]["mesh"]["uv_sets"] == []
    assert receipt["output"]["query_errors"] == []
