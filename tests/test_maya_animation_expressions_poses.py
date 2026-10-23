"""Direct contracts for bounded Maya expression and pose management."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from cgi_pipeline.capabilities.maya.animation.expressions_manage import core as expressions
from cgi_pipeline.capabilities.maya.animation.poses_manage import core as poses
from cgi_pipeline.contracts import ApiContext, ApiContractError, validate_params, validate_spec


ROOT = Path(__file__).resolve().parents[1]


class MayaCmds:
    def __init__(self):
        self.nodes = {
            "|face|ctrl": "transform", "|face|driver": "transform",
            "|other|ctrl": "transform", "|right|ctrl": "transform",
        }
        self.values = {}
        for node in self.nodes:
            self.values.update({
                f"{node}.tx": 0.0, f"{node}.ty": 0.0,
                f"{node}.scaleX": 1.0, f"{node}.enabled": True,
            })
        self.locks = {}
        self.settable = {}
        self.incoming = {}
        self.expression_data = {}
        self.undo_calls = []
        self.snapshot = None
        self.corrupt_expression_readback = False
        self.corrupt_pose_storage = False
        self.corrupt_numeric_plug = ""
        self.fail_set_plug = ""
        self.skip_delete_node = ""

    def _state(self):
        return deepcopy((self.nodes, self.values, self.locks,
                         self.settable, self.expression_data))

    def ls(self, value=None, **kwargs):
        candidates = list(self.nodes)
        if value is not None:
            values = value if isinstance(value, list) else [value]
            candidates = [
                node for node in candidates
                if node in values or node.rsplit("|", 1)[-1] in values
            ]
        node_type = kwargs.get("type")
        if node_type:
            candidates = [node for node in candidates if self.nodes[node] == node_type]
        if kwargs.get("uuid"):
            return [f"uuid:{node}" for node in candidates]
        return candidates

    def objExists(self, value):
        return value in self.nodes or value in self.values

    def getAttr(self, plug, **kwargs):
        if kwargs.get("lock"):
            return self.locks.get(plug, False)
        if kwargs.get("settable"):
            return self.settable.get(plug, True) and not self.locks.get(plug, False)
        return self.values[plug]

    def listConnections(self, plug, **kwargs):
        if kwargs.get("source") and not kwargs.get("destination"):
            return list(self.incoming.get(plug, []))
        return []

    def setAttr(self, plug, value, **_kwargs):
        if plug == self.fail_set_plug:
            raise RuntimeError("injected setAttr failure")
        if plug.endswith(".cgiPoseData") and self.corrupt_pose_storage:
            self.values[plug] = "not-json"
        elif plug == self.corrupt_numeric_plug:
            self.values[plug] = float(value) + 1.0
        else:
            self.values[plug] = value

    def expression(self, node=None, **kwargs):
        if kwargs.get("query"):
            data = self.expression_data[node]
            if kwargs.get("string"):
                if self.corrupt_expression_readback:
                    return ""
                return (data["expression"].replace("|face|", "")
                        .replace(".tx", ".translateX")
                        .replace(".ty", ".translateY"))
            if kwargs.get("alwaysEvaluate"):
                return data["always_evaluate"]
            if kwargs.get("unitConversion"):
                return data["unit_conversion"]
        if kwargs.get("edit"):
            self.expression_data[node] = {
                "expression": kwargs["string"],
                "always_evaluate": kwargs["alwaysEvaluate"],
                "unit_conversion": kwargs["unitConversion"],
            }
            return node
        name = kwargs["name"]
        self.nodes[name] = "expression"
        self.expression_data[name] = {
            "expression": kwargs["string"],
            "always_evaluate": kwargs["alwaysEvaluate"],
            "unit_conversion": kwargs["unitConversion"],
        }
        return name

    def delete(self, nodes):
        for node in nodes:
            if node == self.skip_delete_node:
                continue
            self.nodes.pop(node, None)
            self.expression_data.pop(node, None)

    def createNode(self, node_type, **kwargs):
        base = kwargs["name"]
        node = base
        index = 1
        while node in self.nodes:
            node = f"{base}{index}"; index += 1
        self.nodes[node] = node_type
        return node

    def addAttr(self, node, **kwargs):
        self.values[f"{node}.{kwargs['longName']}"] = ""

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        if kwargs.get("openChunk"):
            self.snapshot = self._state()

    def undo(self):
        self.undo_calls.append({"undo": True})
        (self.nodes, self.values, self.locks,
         self.settable, self.expression_data) = deepcopy(self.snapshot)


def _context(cmds, api_id):
    return ApiContext.from_mapping(
        {"cmds_module": cmds, "_api_id": api_id, "_api_version": "1.0.0"}, "maya"
    )


def _expression(cmds, params):
    return expressions.execute(params, _context(cmds, "maya.animation.expressions.manage"))


def _pose(cmds, params):
    return poses.execute(params, _context(cmds, "maya.animation.poses.manage"))


def test_expression_create_list_update_delete_lifecycle():
    cmds = MayaCmds()
    created = _expression(cmds, {
        "action": "create", "name": "faceExpr",
        "assignments": [{"target": "|face|ctrl.tx", "source": "|face|driver.tx",
                         "multiplier": 2, "offset": 1}],
        "always_evaluate": False, "unit_conversion": "none", "max_items": 10,
    })
    assert created["status"] == "SUCCESS"
    assert created["output"]["expression"] == (
        "ctrl.translateX = ((driver.translateX) * 2.0) + 1.0;"
    )
    listed = _expression(cmds, {"action": "list", "max_items": 10})
    assert listed["output"]["items"][0]["expression_node"] == "faceExpr"
    updated = _expression(cmds, {
        "action": "update", "expression_node": "faceExpr",
        "assignments": [{"target": "|face|ctrl.ty", "value": 3.5}], "max_items": 10,
    })
    assert updated["status"] == "SUCCESS"
    assert updated["output"]["expression"] == (
        "ctrl.translateY = ((3.5) * 1.0) + 0.0;"
    )
    deleted = _expression(cmds, {
        "action": "delete", "expression_nodes": ["faceExpr"], "max_items": 10,
    })
    assert deleted["status"] == "SUCCESS" and "faceExpr" not in cmds.nodes


def test_expression_update_allows_its_own_driven_target_only():
    cmds = MayaCmds()
    assert _expression(cmds, {
        "action": "create", "name": "faceExpr",
        "assignments": [{"target": "|face|ctrl.ty", "value": 1}],
    })["status"] == "SUCCESS"
    cmds.settable["|face|ctrl.ty"] = False
    cmds.incoming["|face|ctrl.ty"] = ["faceExpr.output[0]"]

    updated = _expression(cmds, {
        "action": "update", "expression_node": "faceExpr",
        "assignments": [{"target": "|face|ctrl.ty", "value": 3.5}],
    })
    assert updated["status"] == "SUCCESS"

    cmds.nodes["otherExpr"] = "expression"
    cmds.incoming["|face|ctrl.ty"] = ["otherExpr.output[0]"]
    before = deepcopy(cmds.expression_data)
    rejected = _expression(cmds, {
        "action": "update", "expression_node": "faceExpr",
        "assignments": [{"target": "|face|ctrl.ty", "value": 4.5}],
    })
    assert rejected["status"] == "ERROR"
    assert cmds.expression_data == before


def test_expression_rejects_unsafe_or_ambiguous_inputs_before_mutation():
    cases = [
        {"action": "create", "name": "bad", "assignments": [
            {"target": "|face|ctrl.tx", "source": None}]},
        {"action": "create", "name": "bad", "assignments": [
            {"target": "|face|ctrl.tx", "source": "|face|driver.tx", "value": 1}]},
        {"action": "create", "name": "bad", "assignments": [
            {"target": "ctrl.tx", "value": 1}]},
        {"action": "create", "name": "bad", "assignments": [
            {"target": "|face|ctrl.missing", "value": 1}]},
        {"action": "list", "max_items": True},
        {"action": "list", "max_items": "10"},
        {"action": "list", "max_items": 2001},
    ]
    for params in cases:
        cmds = MayaCmds()
        result = _expression(cmds, params)
        assert result["status"] == "ERROR" and cmds.undo_calls == []
    cmds = MayaCmds(); cmds.settable["|face|ctrl.tx"] = False
    result = _expression(cmds, {"action": "create", "name": "bad", "assignments": [
        {"target": "|face|ctrl.tx", "value": 1}]})
    assert result["status"] == "ERROR" and cmds.undo_calls == []
    cmds = MayaCmds()
    assert _expression(cmds, {"action": "create", "name": "duplicate", "assignments": [
        {"target": "|face|ctrl.tx", "value": 1}]})["status"] == "SUCCESS"
    before = len(cmds.undo_calls)
    result = _expression(cmds, {"action": "create", "name": "duplicate", "assignments": [
        {"target": "|face|ctrl.ty", "value": 2}]})
    assert result["status"] == "ERROR" and len(cmds.undo_calls) == before


def test_expression_failures_undo_create_update_and_partial_delete():
    cmds = MayaCmds(); cmds.corrupt_expression_readback = True
    failed = _expression(cmds, {"action": "create", "name": "badReadback",
        "assignments": [{"target": "|face|ctrl.tx", "value": 1}]})
    assert failed["status"] == "ERROR" and "badReadback" not in cmds.nodes
    assert cmds.undo_calls[-1] == {"undo": True}

    cmds = MayaCmds()
    assert _expression(cmds, {"action": "create", "name": "first", "assignments": [
        {"target": "|face|ctrl.tx", "value": 1}]})["status"] == "SUCCESS"
    assert _expression(cmds, {"action": "create", "name": "second", "assignments": [
        {"target": "|face|ctrl.ty", "value": 2}]})["status"] == "SUCCESS"
    before = deepcopy(cmds.expression_data)
    cmds.corrupt_expression_readback = True
    failed = _expression(cmds, {"action": "update", "expression_node": "first",
        "assignments": [{"target": "|face|ctrl.tx", "value": 9}]})
    assert failed["status"] == "ERROR" and cmds.expression_data == before
    cmds.corrupt_expression_readback = False; cmds.skip_delete_node = "second"
    failed = _expression(cmds, {"action": "delete",
        "expression_nodes": ["first", "second"], "max_items": 10})
    assert failed["status"] == "ERROR" and {"first", "second"} <= set(cmds.nodes)


def test_pose_save_list_load_overwrite_and_explicit_mirror():
    cmds = MayaCmds(); cmds.values["|face|ctrl.tx"] = 2.0
    saved = _pose(cmds, {"action": "save", "name": "smile", "nodes": ["|face|ctrl"],
        "attributes": ["tx", "scaleX"], "max_items": 10})
    assert saved["status"] == "SUCCESS" and saved["output"]["value_count"] == 2
    listed = _pose(cmds, {"action": "list", "max_items": 10})
    assert listed["output"]["items"][0]["name"] == "smile"
    cmds.values["|face|ctrl.tx"] = 8.0
    loaded = _pose(cmds, {"action": "load", "name": "smile", "max_items": 10})
    assert loaded["status"] == "SUCCESS" and cmds.values["|face|ctrl.tx"] == 2.0
    cmds.values["|face|ctrl.tx"] = 4.0
    overwritten = _pose(cmds, {"action": "save", "name": "smile", "nodes": ["|face|ctrl"],
        "attributes": ["tx", "scaleX"], "overwrite": True, "max_items": 10})
    assert overwritten["output"]["overwritten"] is True
    mirrored = _pose(cmds, {"action": "mirror", "name": "smile",
        "node_map": {"|face|ctrl": "|right|ctrl"},
        "attribute_multipliers": {"tx": -1, "scaleX": 1}, "max_items": 10})
    assert mirrored["status"] == "SUCCESS"
    assert cmds.values["|right|ctrl.tx"] == -4.0


def test_pose_rejects_bad_capture_or_mapping_before_mutation():
    cases = [
        {"action": "save", "name": "bad", "nodes": ["ctrl"], "attributes": ["tx"]},
        {"action": "save", "name": "bad", "nodes": ["|face|ctrl"], "attributes": ["missing"]},
        {"action": "list", "max_items": True},
        {"action": "list", "max_items": "10"},
        {"action": "list", "max_items": 2001},
    ]
    for params in cases:
        cmds = MayaCmds(); result = _pose(cmds, params)
        assert result["status"] == "ERROR" and cmds.undo_calls == []
    for bad_value in (float("nan"), float("inf"), (1.0, 2.0)):
        cmds = MayaCmds(); cmds.values["|face|ctrl.tx"] = bad_value
        result = _pose(cmds, {"action": "save", "name": "bad", "nodes": ["|face|ctrl"],
            "attributes": ["tx"]})
        assert result["status"] == "ERROR" and cmds.undo_calls == []

    cmds = MayaCmds()
    assert _pose(cmds, {"action": "save", "name": "pose", "nodes": ["|face|ctrl"],
        "attributes": ["tx"]})["status"] == "SUCCESS"
    for params in (
        {"action": "mirror", "name": "pose", "attribute_multipliers": {"tx": -1}},
        {"action": "mirror", "name": "pose", "node_map": {"|face|ctrl": "|right|ctrl"}},
        {"action": "mirror", "name": "pose", "node_map": {"|face|ctrl": 3},
         "attribute_multipliers": {"tx": -1}},
    ):
        before = len(cmds.undo_calls); result = _pose(cmds, params)
        assert result["status"] == "ERROR" and len(cmds.undo_calls) == before
    cmds.locks["|face|ctrl.tx"] = True
    result = _pose(cmds, {"action": "load", "name": "pose"})
    assert result["status"] == "ERROR"
    cmds.locks.clear(); cmds.settable["|face|ctrl.tx"] = False
    result = _pose(cmds, {"action": "load", "name": "pose"})
    assert result["status"] == "ERROR"

    cmds = MayaCmds()
    assert _pose(cmds, {"action": "save", "name": "pair",
        "nodes": ["|face|ctrl", "|face|driver"], "attributes": ["tx"]})["status"] == "SUCCESS"
    result = _pose(cmds, {"action": "mirror", "name": "pair", "node_map": {
        "|face|ctrl": "|right|ctrl", "|face|driver": "|right|ctrl"},
        "attribute_multipliers": {"tx": -1}})
    assert result["status"] == "ERROR" and "more than once" in result["error"]


def test_pose_failures_undo_storage_and_application():
    cmds = MayaCmds(); cmds.corrupt_pose_storage = True
    failed = _pose(cmds, {"action": "save", "name": "bad", "nodes": ["|face|ctrl"],
        "attributes": ["tx"]})
    assert failed["status"] == "ERROR"
    assert not any(node.startswith("CGIPose_") for node in cmds.nodes)
    assert cmds.undo_calls[-1] == {"undo": True}

    cmds = MayaCmds(); cmds.values["|face|ctrl.tx"] = 2.0
    assert _pose(cmds, {"action": "save", "name": "pose", "nodes": ["|face|ctrl"],
        "attributes": ["tx"]})["status"] == "SUCCESS"
    cmds.values["|face|ctrl.tx"] = 7.0; cmds.corrupt_numeric_plug = "|face|ctrl.tx"
    failed = _pose(cmds, {"action": "load", "name": "pose"})
    assert failed["status"] == "ERROR" and cmds.values["|face|ctrl.tx"] == 7.0
    assert cmds.undo_calls[-1] == {"undo": True}


def test_manifests_validate_and_do_not_accept_raw_expression_source():
    paths = [
        ROOT / "src/cgi_pipeline/capabilities/maya/animation/expressions_manage/capability.yaml",
        ROOT / "src/cgi_pipeline/capabilities/maya/animation/poses_manage/capability.yaml",
    ]
    specs = [validate_spec(yaml.safe_load(path.read_text("utf-8")), str(path)) for path in paths]
    assert all(spec["implementation"]["status"] == "implemented" for spec in specs)
    assert specs[1]["idempotent"] is False
    try:
        validate_params(specs[0], {"action": "create", "expression": "python(\"bad\")"})
    except ApiContractError as exc:
        assert "unknown API inputs" in str(exc)
    else:
        raise AssertionError("raw expression source was accepted")
