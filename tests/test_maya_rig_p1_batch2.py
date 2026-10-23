"""Contract tests for generic Maya rig framework and joint P1 APIs."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from cgi_pipeline.capabilities.maya.rig import rig_p1
from cgi_pipeline.contracts import ApiContext, validate_spec


ROOT = Path(__file__).resolve().parents[1]


def _context(api_id, cmds):
    return ApiContext.from_mapping(
        {"_api_id": api_id, "_api_version": "1.0.0", "cmds_module": cmds}, "maya"
    )


class FrameworkCmds:
    children = {
        "|rig": ["|rig|FitSkeleton", "|rig|ctl"],
        "|rig|FitSkeleton": [],
        "|rig|ctl": ["|rig|ctl|ctlShape"],
        "|rig|ctl|ctlShape": [],
    }

    def ls(self, node=None, **kwargs):
        if kwargs.get("assemblies"):
            return ["|rig"]
        if kwargs.get("type") == "HIKCharacterNode":
            return ["character1"]
        if kwargs.get("uuid"):
            return ["uuid:" + str(node)]
        return [node] if node in self.children else []

    def listRelatives(self, node, **kwargs):
        if kwargs.get("parent"):
            parts = node.rsplit("|", 1)
            return [parts[0]] if parts[0] else []
        if kwargs.get("shapes"):
            return [x for x in self.children.get(node, []) if x.endswith("Shape")]
        return list(self.children.get(node, []))

    def nodeType(self, node):
        if node.endswith("Shape"):
            return "nurbsCurve"
        return "joint" if node.endswith("FitSkeleton") else "transform"

    def referenceQuery(self, node, **kwargs):
        return False

    def objExists(self, plug):
        return plug == "|rig|FitSkeleton.comp_type"


def test_framework_inspect_is_deterministic_bounded_and_read_only():
    cmds = FrameworkCmds()
    context = _context("maya.rig.framework.inspect", cmds)
    first = rig_p1.inspect_framework({"roots": ["|"], "max_nodes": 20, "max_depth": 4}, context)
    second = rig_p1.inspect_framework({"roots": ["|"], "max_nodes": 20, "max_depth": 4}, context)
    assert first["status"] == "SUCCESS"
    assert first["output"]["snapshot_hash"] == second["output"]["snapshot_hash"]
    assert [row["path"] for row in first["output"]["snapshot"]["nodes"]] == sorted(
        row["path"] for row in first["output"]["snapshot"]["nodes"]
    )
    assert {row["name"] for row in first["output"]["frameworks"]} == {
        "AdvancedSkeleton", "HumanIK", "mGear"
    }


def test_framework_inspect_marks_node_bound_truncation():
    receipt = rig_p1.inspect_framework(
        {"roots": ["|"], "max_nodes": 1, "max_depth": 4},
        _context("maya.rig.framework.inspect", FrameworkCmds()),
    )
    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["node_count"] == 1
    assert receipt["output"]["truncated"] is True


def test_framework_inspect_reports_limit_errors():
    receipt = rig_p1.inspect_framework(
        {"roots": ["|"], "max_nodes": 0, "max_depth": 4},
        _context("maya.rig.framework.inspect", FrameworkCmds()),
    )
    assert receipt["status"] == "ERROR"
    assert receipt["error_code"] == "MAYA_RIG_FRAMEWORK_INSPECT_FAILED"

    non_integer = rig_p1.inspect_framework(
        {"roots": ["|"], "max_nodes": 2.5, "max_depth": 4},
        _context("maya.rig.framework.inspect", FrameworkCmds()),
    )
    assert non_integer["status"] == "ERROR"


def test_framework_inspect_rejects_empty_missing_pattern_and_ambiguous_roots():
    cmds = FrameworkCmds()
    context = _context("maya.rig.framework.inspect", cmds)
    for roots in ([], ["|missing"], ["rig*"]):
        receipt = rig_p1.inspect_framework({"roots": roots}, context)
        assert receipt["status"] == "ERROR"

    original_ls = cmds.ls
    cmds.ls = lambda node=None, **kwargs: (
        ["|one|rig", "|two|rig"] if node == "rig" else original_ls(node, **kwargs)
    )
    ambiguous = rig_p1.inspect_framework({"roots": ["rig"]}, context)
    assert ambiguous["status"] == "ERROR"


class JointCmds:
    def __init__(self):
        self.nodes = {"|root_L": "joint"}
        self.attrs = {"|root_L.jointOrientX": 0.0, "|root_L.jointOrientY": 0.0,
                      "|root_L.jointOrientZ": 0.0}
        self.undo_calls = []
        self.limits = []
        self.limit_values = {}
        self._snapshot = None

    def ls(self, node=None, **kwargs):
        return [node] if node in self.nodes else []

    def nodeType(self, node):
        return self.nodes[node]

    def createNode(self, node_type, **kwargs):
        parent = kwargs.get("parent", "")
        node = f"{parent}|{kwargs['name']}" if parent else f"|{kwargs['name']}"
        self.nodes[node] = node_type
        for axis in "XYZ":
            self.attrs[f"{node}.jointOrient{axis}"] = 0.0
        return node

    def xform(self, *args, **kwargs):
        return None

    def setAttr(self, plug, value):
        self.attrs[plug] = value

    def getAttr(self, plug):
        return self.attrs[plug]

    def transformLimits(self, node, **kwargs):
        if kwargs.get("query"):
            for key, value in kwargs.items():
                if key != "query" and value is True:
                    return list(self.limit_values[(node, key)])
            raise AssertionError("Missing transformLimits query flag")
        for key, value in kwargs.items():
            self.limit_values[(node, key)] = tuple(value)
        self.limits.append((node, kwargs))

    def mirrorJoint(self, root, **kwargs):
        created = root.replace("_L", "_R")
        self.nodes[created] = "joint"
        return [created]

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        if kwargs.get("openChunk"):
            self._snapshot = copy.deepcopy((self.nodes, self.attrs, self.limit_values))

    def undo(self):
        self.undo_calls.append({"undo": True})
        self.nodes, self.attrs, self.limit_values = copy.deepcopy(self._snapshot)


def test_joints_manage_runs_canonical_actions_in_one_chunk():
    cmds = JointCmds()
    receipt = rig_p1.manage_joints(
        {"items": [
            {"action": "create", "name": "child_L", "parent": "|root_L",
             "position": [1, 2, 3], "joint_orient": [0, 10, 0]},
            {"action": "set_orient", "node": "|root_L", "joint_orient": [1, 2, 3]},
            {"action": "set_limit", "node": "|root_L",
             "limits": {"x": {"min": -20, "max": 30}}},
            {"action": "mirror", "root": "|root_L", "search": "_L", "replace": "_R",
             "mirror_plane": "yz"},
        ], "max_items": 10},
        _context("maya.rig.joints.manage", cmds),
    )
    assert receipt["status"] == "SUCCESS"
    assert receipt["output"]["count"] == 4
    assert cmds.undo_calls == [
        {"openChunk": True, "chunkName": "maya.rig.joints.manage"}, {"closeChunk": True}
    ]


def test_joints_manage_rejects_over_limit_before_mutation():
    cmds = JointCmds()
    receipt = rig_p1.manage_joints(
        {"items": [{"action": "set_orient", "node": "|root_L", "joint_orient": [0, 0, 0]}] * 2,
         "max_items": 1},
        _context("maya.rig.joints.manage", cmds),
    )
    assert receipt["status"] == "ERROR"
    assert cmds.undo_calls == []


def test_joints_manage_rolls_back_invalid_mirror_search():
    cmds = JointCmds()
    receipt = rig_p1.manage_joints(
        {"items": [{"action": "mirror", "root": "|root_L", "search": "_X", "replace": "_R"}]},
        _context("maya.rig.joints.manage", cmds),
    )
    assert receipt["status"] == "ERROR"
    assert cmds.undo_calls[-1] == {"undo": True}


def test_joints_manage_rolls_back_an_earlier_success_when_second_action_fails():
    cmds = JointCmds()
    before = copy.deepcopy(cmds.attrs)
    receipt = rig_p1.manage_joints(
        {"items": [
            {"action": "set_orient", "node": "|root_L", "joint_orient": [1, 2, 3]},
            {"action": "mirror", "root": "|root_L", "search": "_X", "replace": "_R"},
        ]},
        _context("maya.rig.joints.manage", cmds),
    )
    assert receipt["status"] == "ERROR"
    assert cmds.attrs == before
    assert cmds.undo_calls[-1] == {"undo": True}


def test_joints_manage_rolls_back_orientation_readback_mismatch():
    class MismatchCmds(JointCmds):
        def getAttr(self, plug):
            value = super().getAttr(plug)
            return value + 1.0 if plug.endswith("jointOrientY") else value

    cmds = MismatchCmds()
    before = copy.deepcopy(cmds.attrs)
    receipt = rig_p1.manage_joints(
        {"items": [{"action": "set_orient", "node": "|root_L", "joint_orient": [1, 2, 3]}]},
        _context("maya.rig.joints.manage", cmds),
    )
    assert receipt["status"] == "ERROR"
    assert "readback mismatch" in receipt["error"]
    assert cmds.attrs == before
    assert cmds.undo_calls[-1] == {"undo": True}


def test_joints_manage_rejects_empty_mirror_replacement():
    cmds = JointCmds()
    receipt = rig_p1.manage_joints(
        {"items": [{"action": "mirror", "root": "|root_L", "search": "_L", "replace": ""}]},
        _context("maya.rig.joints.manage", cmds),
    )
    assert receipt["status"] == "ERROR"
    assert "distinct non-empty" in receipt["error"]


def test_joints_manage_rejects_non_finite_or_implicit_boolean_values():
    cmds = JointCmds()
    orient = rig_p1.manage_joints(
        {"items": [{"action": "set_orient", "node": "|root_L",
                    "joint_orient": [0, float("nan"), 0]}]},
        _context("maya.rig.joints.manage", cmds),
    )
    assert orient["status"] == "ERROR"

    mirror = rig_p1.manage_joints(
        {"items": [{"action": "mirror", "root": "|root_L", "search": "_L",
                    "replace": "_R", "mirror_behavior": "false"}]},
        _context("maya.rig.joints.manage", cmds),
    )
    assert mirror["status"] == "ERROR"

    for invalid in (True, "-10"):
        limits = rig_p1.manage_joints(
            {"items": [{"action": "set_limit", "node": "|root_L",
                        "limits": {"x": {"min": invalid, "max": 10}}}]},
            _context("maya.rig.joints.manage", cmds),
        )
        assert limits["status"] == "ERROR"


def test_joints_manage_rejects_missing_limit_key_and_empty_mirror_result():
    cmds = JointCmds()
    missing = rig_p1.manage_joints(
        {"items": [{"action": "set_limit", "node": "|root_L",
                    "limits": {"x": {"min": -10}}}]},
        _context("maya.rig.joints.manage", cmds),
    )
    assert missing["status"] == "ERROR"
    assert "require min and max" in missing["error"]

    cmds = JointCmds()
    cmds.mirrorJoint = lambda *args, **kwargs: []
    empty = rig_p1.manage_joints(
        {"items": [{"action": "mirror", "root": "|root_L", "search": "_L",
                    "replace": "_R"}]},
        _context("maya.rig.joints.manage", cmds),
    )
    assert empty["status"] == "ERROR"
    assert cmds.undo_calls[-1] == {"undo": True}


def test_new_manifests_validate_without_generating_catalog():
    expected_levels = {
        "framework/inspect/capability.yaml": "live_tested",
        "joints/manage/capability.yaml": "contract_tested",
    }
    for relative, expected_level in expected_levels.items():
        path = ROOT / "src" / "cgi_pipeline" / "capabilities" / "maya" / "rig" / relative
        spec = validate_spec(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
        assert spec["implementation"]["status"] == "implemented"
        assert spec["verification"]["level"] == expected_level
