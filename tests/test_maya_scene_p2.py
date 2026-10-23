from __future__ import annotations

import copy
from pathlib import Path

import yaml

from cgi_pipeline.capabilities.maya import scene_p2
from cgi_pipeline.contracts import ApiContext, validate_spec


ROOT = Path(__file__).resolve().parents[1]


class FakeCmds:
    def __init__(self):
        self.nodes = {"|root": "transform", "|other|root": "transform"}
        self.values = {}
        self.types = {}
        self.defaults = {}
        self.locks = {}
        self.user_attributes = {node: set() for node in self.nodes}
        for node in self.nodes:
            self.values[f"{node}.visibility"] = True
            for channel in scene_p2._TRS_CHANNELS:
                self.values[f"{node}.{channel}"] = 0.0
                self.locks[f"{node}.{channel}"] = False
        self.undo_calls = []
        self.snapshot = None
        self.fail_add = ""
        self.ignore_unlock = ""

    def ls(self, node=None, **kwargs):
        if kwargs.get("uuid"):
            return ["uuid:" + str(node)] if node in self.nodes else []
        if node in self.nodes:
            return [node]
        if node == "root":
            return ["|root", "|other|root"]
        return []

    def nodeType(self, node):
        return self.nodes[node]

    def attributeQuery(self, attribute, **kwargs):
        if kwargs.get("listDefault"):
            return [self.defaults[f"{kwargs['node']}.{attribute}"]]
        return attribute in self.user_attributes[kwargs["node"]]

    def listAttr(self, node, **kwargs):
        assert kwargs == {"userDefined": True}
        return sorted(self.user_attributes[node])

    def addAttr(self, node, **kwargs):
        attribute = kwargs["longName"]
        if attribute == self.fail_add:
            raise RuntimeError("injected add failure")
        self.user_attributes[node].add(attribute)
        plug = f"{node}.{attribute}"
        self.types[plug] = kwargs.get("dataType") or kwargs.get("attributeType")
        fallback = "" if self.types[plug] == "string" else 0
        self.defaults[plug] = kwargs.get("defaultValue", fallback)
        self.values[plug] = self.defaults[plug]

    def deleteAttr(self, plug):
        node, attribute = plug.rsplit(".", 1)
        self.user_attributes[node].remove(attribute)
        self.values.pop(plug, None)
        self.types.pop(plug, None)
        self.defaults.pop(plug, None)

    def getAttr(self, plug, **kwargs):
        if kwargs.get("type"):
            return self.types[plug]
        if kwargs.get("lock"):
            return self.locks.get(plug, False)
        return self.values[plug]

    def setAttr(self, plug, *values, **kwargs):
        if "lock" in kwargs:
            if plug == self.ignore_unlock and kwargs["lock"] is False:
                return
            self.locks[plug] = kwargs["lock"]
        else:
            self.values[plug] = values[0]

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        if kwargs.get("openChunk"):
            self.snapshot = copy.deepcopy(
                (self.values, self.types, self.defaults, self.locks, self.user_attributes)
            )

    def undo(self):
        self.undo_calls.append({"undo": True})
        (self.values, self.types, self.defaults,
         self.locks, self.user_attributes) = copy.deepcopy(self.snapshot)


def _context(cmds, api_id):
    return ApiContext.from_mapping(
        {"cmds_module": cmds, "_api_id": api_id, "_api_version": "1.0.0"}, "maya"
    )


def test_resolve_nodes_returns_stable_identity_and_rejects_ambiguity():
    cmds = FakeCmds()
    result = scene_p2.resolve_nodes(
        {"nodes": ["|root"]}, _context(cmds, "maya.scene.nodes.resolve")
    )
    assert result["status"] == "SUCCESS"
    assert result["output"]["items"] == [{
        "input": "|root", "path": "|root", "uuid": "uuid:|root", "node_type": "transform"
    }]
    ambiguous = scene_p2.resolve_nodes(
        {"nodes": ["root"]}, _context(cmds, "maya.scene.nodes.resolve")
    )
    assert ambiguous["status"] == "ERROR"
    assert "ambiguous" in ambiguous["error"]


def test_attribute_schema_adds_and_deletes_in_atomic_transactions():
    cmds = FakeCmds()
    added = scene_p2.manage_attribute_schema(
        {"items": [{"action": "add", "node": "|root", "attribute": "weight",
                    "attribute_type": "double", "default": 0.5}]},
        _context(cmds, "maya.scene.attributes.schema"),
    )
    assert added["status"] == "SUCCESS"
    assert cmds.values["|root.weight"] == 0.5
    assert cmds.defaults["|root.weight"] == 0.5
    deleted = scene_p2.manage_attribute_schema(
        {"items": [{"action": "delete", "node": "|root", "attribute": "weight"}]},
        _context(cmds, "maya.scene.attributes.schema"),
    )
    assert deleted["status"] == "SUCCESS"
    assert "weight" not in cmds.user_attributes["|root"]


def test_attribute_schema_rolls_back_partial_native_failure():
    cmds = FakeCmds(); cmds.fail_add = "broken"
    result = scene_p2.manage_attribute_schema(
        {"items": [
            {"action": "add", "node": "|root", "attribute": "first"},
            {"action": "add", "node": "|root", "attribute": "broken"},
        ]}, _context(cmds, "maya.scene.attributes.schema"),
    )
    assert result["status"] == "ERROR"
    assert cmds.user_attributes["|root"] == set()
    assert cmds.undo_calls[-1] == {"undo": True}


def test_attribute_schema_rejects_duplicate_plugs_before_mutation():
    cmds = FakeCmds()
    result = scene_p2.manage_attribute_schema(
        {"items": [
            {"action": "add", "node": "|root", "attribute": "same"},
            {"action": "add", "node": "|root", "attribute": "same"},
        ]}, _context(cmds, "maya.scene.attributes.schema"),
    )
    assert result["status"] == "ERROR"
    assert cmds.user_attributes["|root"] == set()
    assert cmds.undo_calls == []


def test_node_state_edits_and_verifies_visibility_and_trs_locks():
    cmds = FakeCmds()
    result = scene_p2.manage_node_state(
        {"items": [{"node": "|root", "visibility": False, "lock_trs": True}]},
        _context(cmds, "maya.scene.nodes.state"),
    )
    assert result["status"] == "SUCCESS"
    assert result["output"]["items"][0]["after"] == {
        "visibility": False, "lock_trs": True
    }


def test_node_state_unlock_requires_every_channel_to_read_back_unlocked():
    cmds = FakeCmds()
    for channel in scene_p2._TRS_CHANNELS:
        cmds.locks[f"|root.{channel}"] = True
    cmds.ignore_unlock = "|root.scaleZ"
    result = scene_p2.manage_node_state(
        {"items": [{"node": "|root", "lock_trs": False}]},
        _context(cmds, "maya.scene.nodes.state"),
    )
    assert result["status"] == "ERROR"
    assert all(cmds.locks[f"|root.{channel}"] for channel in scene_p2._TRS_CHANNELS)
    assert cmds.undo_calls[-1] == {"undo": True}


def test_scene_p2_rejects_boolean_limits_and_invalid_state_before_mutation():
    cmds = FakeCmds()
    limited = scene_p2.resolve_nodes(
        {"nodes": ["|root"], "max_items": True},
        _context(cmds, "maya.scene.nodes.resolve"),
    )
    invalid = scene_p2.manage_node_state(
        {"items": [{"node": "|root", "visibility": "false"}]},
        _context(cmds, "maya.scene.nodes.state"),
    )
    assert limited["status"] == "ERROR"
    assert invalid["status"] == "ERROR"
    assert cmds.undo_calls == []


def test_scene_p2_manifests_validate():
    expected = {
        "attributes_schema": "maya.scene.attributes.schema",
        "nodes_resolve": "maya.scene.nodes.resolve",
        "nodes_state": "maya.scene.nodes.state",
    }
    for directory, api_id in expected.items():
        path = ROOT / "src" / "cgi_pipeline" / "capabilities" / "maya" / "scene" / directory / "capability.yaml"
        spec = validate_spec(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
        assert spec["api_id"] == api_id
        assert spec["implementation"]["status"] == "implemented"
        assert spec["verification"]["level"] == "contract_tested"
