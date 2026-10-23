"""Direct contracts for the third canonical Maya rig P1 batch."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from cgi_pipeline.capabilities.maya.rig import (
    rig_control_ops,
    rig_deformation_ops,
    rig_relationship_ops,
)
from cgi_pipeline.contracts import ApiContext, validate_spec

ROOT = Path(__file__).resolve().parents[1]

def _context(api_id, cmds):
    return ApiContext.from_mapping(
        {"_api_id": api_id, "_api_version": "1.0.0", "cmds_module": cmds}, "maya"
    )


class FakeBase:
    _ALIASES = {"tx": "translateX", "ty": "translateY", "tz": "translateZ",
                "rx": "rotateX", "ry": "rotateY", "rz": "rotateZ"}

    def __init__(self):
        self.nodes = {}
        self.attrs = {}
        self.undo_calls = []
        self._snapshot = None

    def _state(self):
        return copy.deepcopy({
            key: value for key, value in self.__dict__.items()
            if key not in {"undo_calls", "_snapshot"}
        })

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        if kwargs.get("openChunk"):
            self._snapshot = self._state()

    def undo(self):
        self.undo_calls.append({"undo": True})
        keep = self.undo_calls
        snapshot = copy.deepcopy(self._snapshot)
        self.__dict__.clear()
        self.__dict__.update(snapshot)
        self.undo_calls = keep
        self._snapshot = None

    def ls(self, node=None, **kwargs):
        if isinstance(node, (list, tuple)):
            rows = []
            for item in node:
                rows.extend(self.ls(item, **kwargs) or [])
            return rows
        candidates = []
        if node in self.nodes:
            candidates = [node]
        elif isinstance(node, str):
            candidates = [path for path in self.nodes if path.rsplit("|", 1)[-1] == node]
        node_type = kwargs.get("type")
        if node_type:
            candidates = [path for path in candidates if self.nodes[path] == node_type]
        return candidates

    def nodeType(self, node):
        return self.nodes[node]

    def objExists(self, plug):
        if "." not in plug:
            return bool(self.ls(plug, long=True))
        node = plug.rsplit(".", 1)[0]
        if not self.ls(node, long=True):
            return False
        if plug in self.attrs:
            return True
        node, attribute = plug.rsplit(".", 1)
        aliases = {**self._ALIASES, **{value: key for key, value in self._ALIASES.items()}}
        return f"{node}.{aliases.get(attribute, attribute)}" in self.attrs

    def attributeQuery(self, attribute, **kwargs):
        if kwargs.get("longName"):
            return self._ALIASES.get(attribute, attribute)
        return False

    def setAttr(self, plug, *values, **kwargs):
        if values:
            self.attrs[plug] = values[0] if len(values) == 1 else tuple(values)

    def getAttr(self, plug, **kwargs):
        return self.attrs[plug]

    def delete(self, nodes):
        for node in ([nodes] if isinstance(nodes, str) else nodes):
            self.nodes.pop(node, None)
            for plug in [key for key in self.attrs if key.startswith(node + ".")]:
                self.attrs.pop(plug)


class DeformCmds(FakeBase):
    def __init__(self, target_vertices=4):
        super().__init__()
        self.nodes = {
            "|base": "transform", "|base|baseShape": "mesh",
            "|target": "transform", "|target|targetShape": "mesh",
        }
        self.shapes = {"|base": ["|base|baseShape"], "|target": ["|target|targetShape"]}
        self.parents = {"|base|baseShape": "|base", "|target|targetShape": "|target"}
        self.vertex_counts = {"|base|baseShape": 4, "|target|targetShape": target_vertices}
        self.blend_base = {}
        self.weights = {}

    def listRelatives(self, node, **kwargs):
        if kwargs.get("parent"):
            return [self.parents[node]] if node in self.parents else []
        if kwargs.get("shapes"):
            return self.shapes.get(node, [])
        return []

    def polyEvaluate(self, shape, **kwargs):
        return self.vertex_counts[shape]

    def blendShape(self, *args, **kwargs):
        if kwargs.get("query"):
            node = args[0]
            if kwargs.get("geometry"):
                return [self.blend_base[node]]
            if kwargs.get("weightCount"):
                return len(self.weights.get(node, {}))
        if kwargs.get("edit"):
            node = args[0]
            _base, index, _target, _full = kwargs["target"]
            if kwargs.get("remove"):
                self.weights[node].pop(index, None)
            else:
                self.weights.setdefault(node, {})[index] = 0.0
            return [node]
        node = "|" + kwargs.get("name", "blendShape1")
        self.nodes[node] = "blendShape"
        self.blend_base[node] = args[-1]
        self.weights[node] = {index: 0.0 for index, _target in enumerate(args[:-1])}
        return [node]

    def setAttr(self, plug, *values, **kwargs):
        super().setAttr(plug, *values, **kwargs)
        if ".weight[" in plug and values:
            node, suffix = plug.split(".weight[")
            self.weights[node][int(suffix[:-1])] = values[0]

    def getAttr(self, plug, **kwargs):
        if plug.endswith(".weight") and kwargs.get("multiIndices"):
            return sorted(self.weights[plug[:-7]])
        if ".weight[" in plug:
            node, suffix = plug.split(".weight[")
            return self.weights[node][int(suffix[:-1])]
        return super().getAttr(plug, **kwargs)

    def cluster(self, geometry, **kwargs):
        node, handle = "|cluster1", "|cluster1Handle"
        self.nodes.update({node: "cluster", handle: "transform"})
        self.attrs[node + ".envelope"] = 1.0
        return [node, handle]

    def lattice(self, geometry, **kwargs):
        self.nodes["|ffd1"] = "ffd"; self.attrs["|ffd1.envelope"] = 1.0
        return ["|ffd1"]

    def nonLinear(self, geometry, **kwargs):
        node = "|" + kwargs["type"] + "1"
        self.nodes[node] = "nonLinear"; self.attrs[node + ".envelope"] = 1.0
        return [node]

def test_blendshape_actions_and_deformer_actions_round_trip():
    cmds = DeformCmds()
    context = _context("maya.rig.blendshape.manage", cmds)
    created = rig_deformation_ops.manage_blendshape(
        {"action": "create", "base_mesh": "|base", "target_meshes": ["|target"], "name": "faceBS"},
        context,
    )
    assert created["status"] == "SUCCESS"
    node = created["output"]["blend_shape"]
    assert rig_deformation_ops.manage_blendshape(
        {"action": "set_weight", "blend_shape": node, "index": 0, "weight": 0.75}, context
    )["output"]["after"] == 0.75
    assert rig_deformation_ops.manage_blendshape(
        {"action": "remove_target", "blend_shape": node, "target_mesh": "|target", "index": 0}, context
    )["status"] == "SUCCESS"

    deformer_context = _context("maya.rig.deformers.manage", cmds)
    deformer = rig_deformation_ops.manage_deformers(
        {"action": "create", "geometry": "|base", "deformer_type": "bend"}, deformer_context
    )["output"]["deformer"]
    assert rig_deformation_ops.manage_deformers(
        {"action": "set_envelope", "deformer": deformer, "envelope": 0.25}, deformer_context
    )["output"]["after"] == 0.25
    assert rig_deformation_ops.manage_deformers(
        {"action": "delete", "deformer": deformer}, deformer_context
    )["status"] == "SUCCESS"


def test_deformation_validation_precedes_mutation_and_readback_failure_rolls_back():
    cmds = DeformCmds(target_vertices=3)
    result = rig_deformation_ops.manage_blendshape(
        {"action": "create", "base_mesh": "|base", "target_meshes": ["|target"]},
        _context("maya.rig.blendshape.manage", cmds),
    )
    assert result["status"] == "ERROR" and cmds.undo_calls == []

    cmds = DeformCmds()
    cmds.cluster("|base")
    before = copy.deepcopy(cmds.attrs)
    original_get = cmds.getAttr
    cmds.getAttr = lambda plug, **kwargs: 0.9 if plug.endswith(".envelope") else original_get(plug, **kwargs)
    failed = rig_deformation_ops.manage_deformers(
        {"action": "set_envelope", "deformer": "|cluster1", "envelope": 0.25},
        _context("maya.rig.deformers.manage", cmds),
    )
    assert failed["status"] == "ERROR" and cmds.attrs == before
    assert cmds.undo_calls[-1] == {"undo": True}

class RelationCmds(FakeBase):
    def __init__(self):
        super().__init__()
        self.nodes = {"|driver": "transform", "|driven": "transform", "|pv": "transform",
                      "|root": "joint", "|root|mid": "joint", "|root|mid|end": "joint"}
        self.attrs = {"|driver.tx": 0.0, "|driven.ty": 0.0, "|driven.tz": 0.0}
        self.constraint_targets = {}; self.constraint_aliases = {}; self.curves = {}

    def _constraint(self, node_type, *args, **kwargs):
        if kwargs.get("query"):
            node = args[0]
            if kwargs.get("targetList"):
                return self.constraint_targets[node]
            if kwargs.get("weightAliasList"):
                return self.constraint_aliases[node]
        node = "|" + kwargs.get("name", node_type + "1")
        self.nodes[node] = node_type; self.constraint_targets[node] = list(args[:-1])
        self.constraint_aliases[node] = [f"w{index}" for index in range(len(args) - 1)]
        for alias in self.constraint_aliases[node]: self.attrs[f"{node}.{alias}"] = kwargs["weight"]
        return [node]

    def parentConstraint(self, *args, **kwargs): return self._constraint("parentConstraint", *args, **kwargs)
    def pointConstraint(self, *args, **kwargs): return self._constraint("pointConstraint", *args, **kwargs)
    def orientConstraint(self, *args, **kwargs): return self._constraint("orientConstraint", *args, **kwargs)
    def scaleConstraint(self, *args, **kwargs): return self._constraint("scaleConstraint", *args, **kwargs)
    def aimConstraint(self, *args, **kwargs): return self._constraint("aimConstraint", *args, **kwargs)
    def poleVectorConstraint(self, *args, **kwargs): return self._constraint("poleVectorConstraint", *args, **kwargs)

    def setDrivenKeyframe(self, driven, **kwargs):
        curve = next((name for name, row in self.curves.items()
                      if row["driver"] == kwargs["currentDriver"] and row["driven"] == driven), None)
        if curve is None:
            curve = f"|sdk{len(self.curves)}"; self.nodes[curve] = "animCurveUU"
            self.curves[curve] = {"driver": kwargs["currentDriver"], "driven": driven, "keys": {}}
        self.curves[curve]["keys"][float(kwargs["driverValue"])] = float(kwargs["value"])

    def listConnections(self, plug, **kwargs):
        if plug.endswith(".input"):
            return [self.curves[plug[:-6]]["driver"].lstrip("|")]
        if plug.endswith(".output"):
            return [self.curves[plug[:-7]]["driven"]]
        return [name for name, row in self.curves.items() if row["driven"] == plug]

    def keyframe(self, curve, **kwargs):
        keys = sorted(self.curves[curve]["keys"].items())
        return [key for key, _value in keys] if kwargs.get("floatChange") else [value for _key, value in keys]

    def delete(self, nodes):
        for node in ([nodes] if isinstance(nodes, str) else nodes):
            self.curves.pop(node, None)
            self.constraint_targets.pop(node, None); self.constraint_aliases.pop(node, None)
        super().delete(nodes)

    def listRelatives(self, node, **kwargs):
        if kwargs.get("allDescendents") and node == "|root":
            return ["|root|mid", "|root|mid|end"]
        return []

    def ikHandle(self, **kwargs):
        handle = "|" + kwargs.get("name", "ikHandle1"); effector = "|effector1"
        self.nodes.update({handle: "ikHandle", effector: "ikEffector"})
        self.attrs[handle + ".ikBlend"] = 1.0
        return [handle, effector]

def test_constraint_driven_key_and_ik_canonical_flows():
    cmds = RelationCmds()
    constraint_context = _context("maya.rig.constraints.manage", cmds)
    node = rig_relationship_ops.manage_constraints(
        {"action": "create", "drivers": ["|driver"], "driven": "|driven"}, constraint_context
    )["output"]["constraint"]
    weighted = rig_relationship_ops.manage_constraints(
        {"action": "set_weight", "constraint": node, "target": "|driver", "weight": 0.4},
        constraint_context,
    )
    assert weighted["status"] == "SUCCESS" and weighted["output"]["after"] == 0.4

    driven_context = _context("maya.rig.driven_keys.manage", cmds)
    created = rig_relationship_ops.manage_driven_keys(
        {"action": "create", "driver_attr": "|driver.tx", "driven_attrs": ["|driven.ty"],
         "driver_values": [0, 1], "driven_values": [[0], [2]]}, driven_context,
    )
    assert created["status"] == "SUCCESS" and created["output"]["keys_set"] == 2
    assert cmds.attrs["|driver.tx"] == 0.0 and cmds.attrs["|driven.ty"] == 0.0
    assert rig_relationship_ops.manage_driven_keys(
        {"action": "query", "driver_attr": "|driver.tx", "driven_attrs": ["|driven.ty"]},
        driven_context,
    )["output"]["curve_count"] == 1
    assert rig_relationship_ops.manage_driven_keys(
        {"action": "remove", "driver_attr": "|driver.tx", "driven_attrs": ["|driven.ty"]},
        driven_context,
    )["status"] == "SUCCESS"

    ik_context = _context("maya.rig.ik.manage", cmds)
    handle = rig_relationship_ops.manage_ik(
        {"action": "create", "start_joint": "|root", "end_joint": "|root|mid|end"}, ik_context
    )["output"]["handle"]
    assert rig_relationship_ops.manage_ik(
        {"action": "set_blend", "ik_handle": handle, "blend": 0.35}, ik_context
    )["output"]["after"] == 0.35

@pytest.mark.parametrize("operation,params,api_id", [
    (rig_relationship_ops.manage_constraints,
     {"action": "create", "drivers": [], "driven": "|driven"}, "maya.rig.constraints.manage"),
    (rig_relationship_ops.manage_driven_keys,
     {"action": "create", "driver_attr": "|driver.tx", "driven_attrs": ["|driven.ty"],
      "driver_values": [1, 0], "driven_values": [[1], [0]]}, "maya.rig.driven_keys.manage"),
    (rig_relationship_ops.manage_ik,
     {"action": "create", "start_joint": "|root|mid", "end_joint": "|root"}, "maya.rig.ik.manage"),
])
def test_relationship_validation_rejects_before_mutation(operation, params, api_id):
    cmds = RelationCmds()
    result = operation(params, _context(api_id, cmds))
    assert result["status"] == "ERROR" and cmds.undo_calls == []

def test_constraint_target_readback_mismatch_rolls_back_creation():
    class BadReadback(RelationCmds):
        def parentConstraint(self, *args, **kwargs):
            if kwargs.get("query") and kwargs.get("targetList"):
                return []
            return super().parentConstraint(*args, **kwargs)

    cmds = BadReadback(); before = copy.deepcopy(cmds.nodes)
    result = rig_relationship_ops.manage_constraints(
        {"action": "create", "drivers": ["|driver"], "driven": "|driven"},
        _context("maya.rig.constraints.manage", cmds),
    )
    assert result["status"] == "ERROR" and cmds.nodes == before
    assert cmds.undo_calls[-1] == {"undo": True}

class ControlCmds(FakeBase):
    def __init__(self):
        super().__init__()
        self.nodes = {"|target": "transform", "|parent": "transform"}
        self.matrices = {"|target": list(range(16)), "|parent": [1.0] * 16}
        self.shapes = {}; self.metadata = {}

    def _curve(self, name):
        node, shape = "|" + name, "|" + name + "|" + name + "Shape"
        self.nodes.update({node: "transform", shape: "nurbsCurve"}); self.shapes[node] = [shape]
        self.matrices[node] = [1.0] * 16
        return node

    def circle(self, **kwargs): return [self._curve(kwargs["name"])]
    def curve(self, **kwargs): return self._curve(kwargs["name"])
    def group(self, **kwargs):
        node = "|" + kwargs["name"]; self.nodes[node] = "transform"; self.matrices[node] = [1.0] * 16
        return node
    def xform(self, node, **kwargs):
        if kwargs.get("query"): return list(self.matrices[node])
        self.matrices[node] = list(kwargs["matrix"])
    def parent(self, node, parent, **kwargs): return [node]
    def listRelatives(self, node, **kwargs): return self.shapes.get(node, []) if kwargs.get("shapes") else []
    def attributeQuery(self, name, **kwargs):
        if kwargs.get("longName"): return name
        return (kwargs["node"], name) in self.metadata
    def addAttr(self, node, **kwargs): self.metadata[(node, kwargs["longName"])] = None
    def setAttr(self, plug, *values, **kwargs):
        super().setAttr(plug, *values, **kwargs)
        if ".cgiGuide" in plug: self.metadata[tuple(plug.rsplit(".", 1))] = values[0]
    def getAttr(self, plug, **kwargs):
        if ".cgiGuide" in plug: return self.metadata[tuple(plug.rsplit(".", 1))]
        return super().getAttr(plug, **kwargs)
    def arclen(self, curve): return 5.0

def test_control_and_guide_creation_are_atomic_and_read_back():
    cmds = ControlCmds(); context = _context("maya.rig.controls.create", cmds)
    control = rig_control_ops.create_controls(
        {"kind": "rig_control", "name": "mouth_ctl", "shape": "square",
         "target": "|target", "parent": "|parent", "offset_groups": 2,
         "color_index": 6, "lock_attrs": ["sx", "sy", "sz"]}, context,
    )
    assert control["status"] == "SUCCESS" and len(control["output"]["offset_groups"]) == 2
    guide = rig_control_ops.create_controls(
        {"kind": "guide_curve", "name": "browGuide", "degree": 1,
         "points": [[0, 0, 0], [1, 0, 0]], "cluster_id": "brow.L",
         "display_color_rgb": [0.1, 0.4, 0.8]}, context,
    )
    assert guide["status"] == "SUCCESS" and guide["output"]["arc_length"] == 5.0

def test_control_invalid_data_never_opens_undo_chunk():
    cmds = ControlCmds()
    result = rig_control_ops.create_controls(
        {"kind": "guide_curve", "name": "badGuide", "degree": 3,
         "points": [[0, 0, 0]], "cluster_id": "bad", "display_color_rgb": [1, 1, 1]},
        _context("maya.rig.controls.create", cmds),
    )
    assert result["status"] == "ERROR" and cmds.undo_calls == []

def test_guide_color_readback_mismatch_rolls_back_creation():
    class BadColor(ControlCmds):
        def getAttr(self, plug, **kwargs):
            if plug.endswith(".overrideColorRGB"):
                return [(0.0, 0.0, 0.0)]
            return super().getAttr(plug, **kwargs)

    cmds = BadColor(); before = copy.deepcopy(cmds.nodes)
    result = rig_control_ops.create_controls(
        {"kind": "guide_curve", "name": "badColor", "degree": 1,
         "points": [[0, 0, 0], [1, 0, 0]], "cluster_id": "brow",
         "display_color_rgb": [1, 0, 0]}, _context("maya.rig.controls.create", cmds),
    )
    assert result["status"] == "ERROR" and cmds.nodes == before
    assert cmds.undo_calls[-1] == {"undo": True}

def test_all_seven_manifests_validate_as_contract_tested():
    paths = [
        "blendshape/manage", "constraints/manage", "controls/create", "deformers/manage",
        "driven_keys/manage", "ik/manage", "skin/copy_weights",
    ]
    for relative in paths:
        path = ROOT / "src/cgi_pipeline/capabilities/maya/rig" / relative / "capability.yaml"
        spec = validate_spec(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
        assert spec["implementation"]["status"] == "implemented"
        assert spec["verification"]["level"] == "contract_tested"
