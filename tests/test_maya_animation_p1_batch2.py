"""Contracts for the second Maya animation capability batch."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from cgi_pipeline.capabilities.maya import animation_bake, animation_curves
from cgi_pipeline.contracts import ApiContext, ApiContractError, validate_params, validate_spec


ROOT = Path(__file__).resolve().parents[1]


class AnimationCmds:
    def __init__(self):
        self.nodes = {"|ctrl": "transform", "|other": "transform",
                      "curve_tx": "animCurveTL", "curve_other": "animCurveTL"}
        self.plugs = {"|ctrl.tx", "|other.tx"}
        self.curves = {"|ctrl.tx": ["curve_tx"], "|other.tx": ["curve_other"]}
        self.keys = {
            "curve_tx": {1.0: [0.0, "auto", "auto"], 10.0: [2.0, "auto", "auto"]},
            "curve_other": {1.0: [1.0, "auto", "auto"]},
        }
        self.undo_calls = []
        self.undo_count = 0
        self.snapshot = None
        self.bake_result = 1
        self.fail_tangent_curve = ""
        self.zero_tangent_curve = ""
        self.corrupt_tangent_readback = False
        self.tangent_written = False
        self.fail_readback = False
        self.raise_bake = False
        self.last_bake = {}

    def ls(self, value=None, **_kwargs):
        values = value if isinstance(value, list) else [value]
        return [item for item in values if item in self.nodes]

    def nodeType(self, node):
        return self.nodes[node]

    def objExists(self, value):
        return value in self.nodes or value in self.plugs

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        if kwargs.get("openChunk"):
            self.snapshot = deepcopy(self.keys)

    def undo(self):
        self.undo_count += 1
        self.keys = deepcopy(self.snapshot)

    def bakeResults(self, nodes, **kwargs):
        if self.raise_bake:
            raise RuntimeError("simulated bake failure")
        self.last_bake = {"nodes": list(nodes), **kwargs}
        if self.bake_result > 0:
            for node in nodes:
                for attribute in kwargs["attribute"]:
                    plug = f"{node}.{attribute}"
                    curve = self.curves[plug][0]
                    self.keys[curve][float(kwargs["time"][0])] = [0.0, "auto", "auto"]
        return self.bake_result

    def keyframe(self, target, **kwargs):
        if kwargs.get("name"):
            return list(self.curves.get(target, []))
        curve = self.curves.get(target, [target])[0]
        rows = self.keys.get(curve, {})
        window = kwargs.get("time")
        frames = sorted(frame for frame in rows if not window or window[0] <= frame <= window[1])
        if kwargs.get("keyframeCount"):
            return 0 if self.fail_readback else len(frames)
        if kwargs.get("timeChange"):
            return frames
        if kwargs.get("valueChange"):
            return [rows[frame][0] for frame in frames]
        return []

    def keyTangent(self, curve, **kwargs):
        rows = self.keys[curve]
        window = kwargs.get("time")
        frames = sorted(frame for frame in rows if not window or window[0] <= frame <= window[1])
        if kwargs.get("query") and kwargs.get("inTangentType"):
            values = [rows[frame][1] for frame in frames]
            return ["auto"] * len(values) if self.corrupt_tangent_readback and self.tangent_written else values
        if kwargs.get("query") and kwargs.get("outTangentType"):
            return [rows[frame][2] for frame in frames]
        if curve == self.fail_tangent_curve:
            raise RuntimeError("simulated tangent failure")
        if curve == self.zero_tangent_curve:
            return 0
        for frame in frames:
            if kwargs.get("inTangentType"):
                rows[frame][1] = kwargs["inTangentType"]
            if kwargs.get("outTangentType"):
                rows[frame][2] = kwargs["outTangentType"]
        self.tangent_written = True
        return 1 if frames else 0


def _context(cmds, api_id):
    return ApiContext.from_mapping(
        {"cmds_module": cmds, "_api_id": api_id, "_api_version": "1.0.0"}, "maya")


def test_bake_uses_bounded_official_flags_and_reads_back_keys():
    cmds = AnimationCmds()
    result = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["|ctrl"], "attributes": ["tx"],
         "start_time": 1, "end_time": 10, "sample_by": 1, "simulation": False,
         "disable_implicit_control": False, "preserve_outside_keys": True,
         "sparse": False, "minimize_rotation": True, "max_items": 10},
        _context(cmds, "maya.animation.bake"))
    assert result["status"] == "SUCCESS"
    assert result["output"]["changed_channels"] == 1
    assert cmds.last_bake["animation"] == "objects"
    assert cmds.last_bake["hierarchy"] == "none"
    assert cmds.last_bake["preserveOutsideKeys"] is True
    assert cmds.undo_calls[0]["openChunk"] is True


def test_bake_rejects_invalid_work_and_rolls_back_zero_or_bad_readback():
    context = lambda cmds: _context(cmds, "maya.animation.bake")
    cmds = AnimationCmds()
    invalid = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["|ctrl"], "attributes": ["tx"],
         "start_time": 10, "end_time": 1, "max_items": 10}, context(cmds))
    assert invalid["status"] == "ERROR" and cmds.undo_calls == []
    duplicate = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["|ctrl", "|ctrl"], "attributes": ["tx"],
         "start_time": 1, "end_time": 2, "max_items": 10}, context(cmds))
    assert duplicate["status"] == "ERROR" and cmds.undo_calls == []
    duplicate_attr = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["|ctrl"], "attributes": ["tx", "tx"],
         "start_time": 1, "end_time": 2, "max_items": 10}, context(cmds))
    assert duplicate_attr["status"] == "ERROR" and cmds.undo_calls == []
    nonfinite = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["|ctrl"], "attributes": ["tx"],
         "start_time": float("nan"), "end_time": 2, "max_items": 10}, context(cmds))
    assert nonfinite["status"] == "ERROR" and cmds.undo_calls == []
    simulation_cap = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["|ctrl"], "attributes": ["tx"],
         "start_time": 1, "end_time": 20000, "sample_by": 1,
         "simulation": True, "max_items": 10}, context(cmds))
    assert simulation_cap["status"] == "ERROR" and cmds.undo_calls == []
    cmds = AnimationCmds(); cmds.bake_result = 0
    zero = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["|ctrl"], "attributes": ["tx"],
         "start_time": 1, "end_time": 2, "max_items": 10}, context(cmds))
    assert zero["status"] == "ERROR" and cmds.undo_count == 1
    cmds = AnimationCmds(); cmds.fail_readback = True
    failed = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["|ctrl"], "attributes": ["tx"],
         "start_time": 1, "end_time": 2, "max_items": 10}, context(cmds))
    assert failed["status"] == "ERROR" and cmds.undo_count == 1
    cmds = AnimationCmds(); cmds.raise_bake = True
    raised = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["|ctrl"], "attributes": ["tx"],
         "start_time": 1, "end_time": 2, "max_items": 10}, context(cmds))
    assert raised["status"] == "ERROR" and cmds.undo_count == 1


def test_bake_rejects_ambiguous_short_nodes():
    class AmbiguousCmds(AnimationCmds):
        def ls(self, value=None, **kwargs):
            if value == "ctrl":
                return ["|left|ctrl", "|right|ctrl"]
            return super().ls(value, **kwargs)

    cmds = AmbiguousCmds()
    result = animation_bake.bake_animation(
        {"action": "bake", "nodes": ["ctrl"], "attributes": ["tx"],
         "start_time": 1, "end_time": 2, "max_items": 10},
        _context(cmds, "maya.animation.bake"))
    assert result["status"] == "ERROR"
    assert "ambiguous" in result["error"] and cmds.undo_calls == []


def test_curve_query_is_plug_scoped_and_bounded():
    cmds = AnimationCmds()
    result = animation_curves.manage_curves(
        {"action": "query", "nodes": ["|ctrl"], "attributes": ["tx"],
         "times": [], "max_items": 10, "max_keys": 1},
        _context(cmds, "maya.animation.curves.manage"))
    assert result["status"] == "SUCCESS"
    assert result["output"]["key_count"] == 2
    assert result["output"]["returned_key_count"] == 1
    assert result["output"]["truncated"] is True
    assert result["output"]["items"][0]["plug"] == "|ctrl.tx"


def test_curve_query_returns_empty_for_an_unanimated_plug():
    cmds = AnimationCmds(); cmds.curves["|ctrl.tx"] = []
    result = animation_curves.manage_curves(
        {"action": "query", "nodes": ["|ctrl"], "attributes": ["tx"],
         "times": [], "max_items": 10, "max_keys": 10},
        _context(cmds, "maya.animation.curves.manage"))
    assert result["status"] == "SUCCESS"
    assert result["output"]["key_count"] == 0
    assert result["output"]["items"][0]["curves"] == []


def test_tangent_edit_verifies_readback_and_rolls_back_partial_failure():
    cmds = AnimationCmds(); context = _context(cmds, "maya.animation.curves.manage")
    changed = animation_curves.manage_curves(
        {"action": "set_tangent_types", "nodes": ["|ctrl"], "attributes": ["tx"],
         "start_time": 1, "end_time": 10, "times": [],
         "in_tangent_type": "linear", "out_tangent_type": "flat",
         "max_items": 10, "max_keys": 10}, context)
    assert changed["status"] == "SUCCESS"
    assert all(row[1:] == ["linear", "flat"] for row in cmds.keys["curve_tx"].values())
    cmds = AnimationCmds(); cmds.fail_tangent_curve = "curve_other"
    failed = animation_curves.manage_curves(
        {"action": "set_tangent_types", "nodes": ["|ctrl", "|other"],
         "attributes": ["tx"], "start_time": 1, "end_time": 10, "times": [],
         "in_tangent_type": "linear", "out_tangent_type": "",
         "max_items": 10, "max_keys": 10},
        _context(cmds, "maya.animation.curves.manage"))
    assert failed["status"] == "ERROR" and cmds.undo_count == 1
    assert cmds.keys["curve_tx"][1.0][1] == "auto"


def test_tangent_edit_rejects_missing_keys_and_dangerous_inputs():
    cmds = AnimationCmds(); context = _context(cmds, "maya.animation.curves.manage")
    no_range = animation_curves.manage_curves(
        {"action": "set_tangent_types", "nodes": ["|ctrl"], "attributes": ["tx"],
         "times": [], "in_tangent_type": "linear", "out_tangent_type": "",
         "max_items": 10, "max_keys": 10}, context)
    assert no_range["status"] == "ERROR" and cmds.undo_calls == []
    bad_type = animation_curves.manage_curves(
        {"action": "set_tangent_types", "nodes": ["|ctrl"], "attributes": ["tx"],
         "times": [1], "in_tangent_type": "fixed", "out_tangent_type": "",
         "max_items": 10, "max_keys": 10}, context)
    assert bad_type["status"] == "ERROR" and cmds.undo_calls == []
    no_keys = animation_curves.manage_curves(
        {"action": "set_tangent_types", "nodes": ["|ctrl"], "attributes": ["tx"],
         "times": [99], "in_tangent_type": "linear", "out_tangent_type": "",
         "max_items": 10, "max_keys": 10}, context)
    assert no_keys["status"] == "ERROR" and cmds.undo_calls == []
    cmds.curves["|other.tx"] = []
    partial = animation_curves.manage_curves(
        {"action": "set_tangent_types", "nodes": ["|ctrl", "|other"],
         "attributes": ["tx"], "times": [1],
         "in_tangent_type": "linear", "out_tangent_type": "",
         "max_items": 10, "max_keys": 10}, context)
    assert partial["status"] == "ERROR" and cmds.undo_calls == []


def test_tangent_zero_change_and_bad_readback_roll_back():
    values = {
        "action": "set_tangent_types", "nodes": ["|ctrl"], "attributes": ["tx"],
        "times": [1], "in_tangent_type": "linear", "out_tangent_type": "",
        "max_items": 10, "max_keys": 10,
    }
    cmds = AnimationCmds(); cmds.zero_tangent_curve = "curve_tx"
    zero = animation_curves.manage_curves(
        values, _context(cmds, "maya.animation.curves.manage"))
    assert zero["status"] == "ERROR" and cmds.undo_count == 1
    cmds = AnimationCmds(); cmds.corrupt_tangent_readback = True
    corrupt = animation_curves.manage_curves(
        values, _context(cmds, "maya.animation.curves.manage"))
    assert corrupt["status"] == "ERROR" and cmds.undo_count == 1
    assert cmds.keys["curve_tx"][1.0][1] == "auto"


def test_curve_manifest_rejects_weighted_tangent_inputs():
    relative = "src/cgi_pipeline/capabilities/maya/animation/curves_manage/capability.yaml"
    spec = validate_spec(yaml.safe_load((ROOT / relative).read_text("utf-8")), relative)
    try:
        validate_params(spec, {
            "action": "query", "nodes": ["|ctrl"], "attributes": ["tx"],
            "weighted_tangents": True,
        })
    except ApiContractError as exc:
        assert "unknown API inputs" in str(exc)
    else:
        raise AssertionError("weighted tangent input was accepted")


def test_animation_manifests_validate():
    for relative in (
        "src/cgi_pipeline/capabilities/maya/animation/bake/capability.yaml",
        "src/cgi_pipeline/capabilities/maya/animation/curves_manage/capability.yaml",
    ):
        spec = validate_spec(yaml.safe_load((ROOT / relative).read_text("utf-8")), relative)
        assert spec["implementation"]["status"] == "implemented"
        assert spec["verification"]["level"] == "contract_tested"
