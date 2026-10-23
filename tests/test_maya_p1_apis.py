"""Contract tests for the first reconciled Maya P1 capability batch."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from cgi_pipeline.capabilities.maya import animation_core
from cgi_pipeline.capabilities.maya.rig.skin import core as skin_core
from cgi_pipeline.capabilities.maya.rig.skin.query_weights import handler as skin_query
from cgi_pipeline.contracts import ApiContext, validate_spec


ROOT = Path(__file__).resolve().parents[1]


def _context(cmds, api_id):
    return ApiContext.from_mapping(
        {"cmds_module": cmds, "_api_id": api_id, "_api_version": "1.0.0"}, "maya"
    )


class AnimationCmds:
    def __init__(self):
        self.current = 1.0
        self.range = {"minTime": 1.0, "maxTime": 24.0,
                      "animationStartTime": 1.0, "animationEndTime": 48.0}
        self.nodes = {"|ctrl": {"tx": 0.0}, "|other": {"tx": 0.0},
                      "|left|dup": {"tx": 0.0}, "|right|dup": {"tx": 0.0}}
        self.keys = {"|ctrl": {"tx": {1.0: 0.0}}}
        self.undo_calls = []
        self.undo_count = 0
        self._snapshot = None

    def ls(self, value=None, **_kwargs):
        if value in self.nodes:
            return [value]
        return [path for path in self.nodes if path.rsplit("|", 1)[-1] == value]

    def currentTime(self, value=None, **kwargs):
        if kwargs.get("query"):
            return self.current
        self.current = float(value)

    def playbackOptions(self, **kwargs):
        if kwargs.get("query"):
            for name in self.range:
                if kwargs.get(name):
                    return self.range[name]
        else:
            self.range.update({name: float(value) for name, value in kwargs.items()})

    def currentUnit(self, **kwargs):
        assert kwargs == {"query": True, "time": True}
        return "film"

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        if kwargs.get("openChunk"):
            self._snapshot = (self.current, deepcopy(self.range),
                              deepcopy(self.nodes), deepcopy(self.keys))

    def undo(self):
        self.undo_count += 1
        self.current, self.range, self.nodes, self.keys = deepcopy(self._snapshot)

    def setAttr(self, plug, value):
        node, attribute = plug.rsplit(".", 1)
        self.nodes[node][attribute] = float(value)

    def setKeyframe(self, node, **kwargs):
        attributes = kwargs.get("attribute") or ["tx"]
        frame = float(kwargs.get("time", self.current))
        for attribute in attributes:
            self.keys.setdefault(node, {}).setdefault(attribute, {})[frame] = self.nodes[node][attribute]
        return len(attributes)

    def keyframe(self, node, **kwargs):
        attributes = kwargs.get("attribute") or list(self.keys.get(node, {}))
        times = []
        for attribute in attributes:
            times.extend(self.keys.get(node, {}).get(attribute, {}))
        if kwargs.get("time"):
            start, end = kwargs["time"]
            times = [value for value in times if start <= value <= end]
        return times

    def cutKey(self, node, **kwargs):
        attributes = kwargs.get("attribute") or list(self.keys.get(node, {}))
        start, end = kwargs.get("time", (float("-inf"), float("inf")))
        changed = 0
        for attribute in attributes:
            curve = self.keys.get(node, {}).get(attribute, {})
            matched = [frame for frame in curve if start <= frame <= end]
            for frame in matched:
                del curve[frame]
            changed += len(matched)
        return changed


class SkinCmds:
    def __init__(self):
        self.nodes = {
            "|mesh": "transform", "|mesh|meshShape": "mesh",
            "|j1": "joint", "|j2": "joint",
        }
        self.history = []
        self.influences = []
        self.weights = {0: [], 1: []}
        self.undo_calls = []
        self.undo_count = 0
        self._snapshot = None
        self.corrupt_after_write = False
        self.written = False

    def ls(self, value=None, **kwargs):
        values = list(value) if isinstance(value, list) else list(self.nodes) if value is None else (
            [value] if value in self.nodes else [path for path in self.nodes if path.rsplit("|", 1)[-1] == value]
        )
        node_type = kwargs.get("type")
        return [item for item in values if item in self.nodes and (not node_type or self.nodes[item] == node_type)]

    def nodeType(self, node):
        return self.nodes[node]

    def listRelatives(self, node, **kwargs):
        if kwargs.get("parent") and node == "|mesh|meshShape":
            return ["|mesh"]
        if kwargs.get("shapes") and node == "|mesh":
            return ["|mesh|meshShape"]
        return []

    def listHistory(self, _shape, **_kwargs):
        return list(self.history)

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        if kwargs.get("openChunk"):
            self._snapshot = (deepcopy(self.nodes), list(self.history),
                              list(self.influences), deepcopy(self.weights))

    def undo(self):
        self.undo_count += 1
        self.nodes, self.history, self.influences, self.weights = deepcopy(self._snapshot)

    def skinCluster(self, *args, **kwargs):
        if kwargs.get("query") and kwargs.get("influence"):
            return list(self.influences)
        if kwargs.get("edit") and kwargs.get("unbind"):
            cluster = args[0]
            self.history.remove(cluster)
            del self.nodes[cluster]
            return None
        cluster = kwargs.get("name") or "skinCluster1"
        self.nodes[cluster] = "skinCluster"
        self.history = [cluster]
        self.influences = list(args[:-1])
        self.weights = {0: [1.0, 0.0], 1: [0.0, 1.0]}
        return [cluster]

    def polyEvaluate(self, _shape, **kwargs):
        assert kwargs == {"vertex": True}
        return 2

    def skinPercent(self, _cluster, component, **kwargs):
        vertex = int(component.rsplit("[", 1)[1][:-1])
        if kwargs.get("query"):
            if self.corrupt_after_write and self.written:
                return [0.5, 0.5]
            return list(self.weights[vertex])
        pairs = dict(kwargs["transformValue"])
        self.weights[vertex] = [pairs.get(influence, 0.0) for influence in self.influences]
        self.written = True


def test_new_manifests_are_valid_and_contract_tested():
    paths = [
        "src/cgi_pipeline/capabilities/maya/animation/time_manage/capability.yaml",
        "src/cgi_pipeline/capabilities/maya/animation/keyframes_manage/capability.yaml",
        "src/cgi_pipeline/capabilities/maya/rig/skin/bind/capability.yaml",
        "src/cgi_pipeline/capabilities/maya/rig/skin/set_weights/capability.yaml",
    ]
    for relative in paths:
        spec = validate_spec(yaml.safe_load((ROOT / relative).read_text(encoding="utf-8")), relative)
        assert spec["implementation"]["status"] == "implemented"
        assert spec["verification"]["level"] == "contract_tested"
        assert {"summary", "usage", "scenario", "safety"} <= set(spec["help"])
        assert all(isinstance(value, str) and value for value in spec["help"].values())


def test_time_query_and_set_range_use_one_canonical_contract():
    cmds = AnimationCmds()
    queried = animation_core.manage_time(
        {"action": "query"}, _context(cmds, "maya.animation.time.manage")
    )
    assert queried["status"] == "SUCCESS"
    assert queried["output"]["timeline"]["playback_end"] == 24.0
    changed = animation_core.manage_time(
        {"action": "set_range", "playback_start": 10, "playback_end": 20},
        _context(cmds, "maya.animation.time.manage"),
    )
    assert changed["status"] == "SUCCESS"
    assert changed["output"]["timeline"]["playback_start"] == 10.0
    assert cmds.undo_calls[0]["openChunk"] is True
    assert cmds.undo_calls[-1]["closeChunk"] is True


def test_invalid_time_range_does_not_open_an_undo_chunk():
    cmds = AnimationCmds()
    result = animation_core.manage_time(
        {"action": "set_range", "playback_start": 20, "playback_end": 10},
        _context(cmds, "maya.animation.time.manage"),
    )
    assert result["status"] == "ERROR"
    assert cmds.range["minTime"] == 1.0
    assert cmds.undo_calls == []


def test_set_current_does_not_claim_maya_undo_support():
    cmds = AnimationCmds()
    result = animation_core.manage_time(
        {"action": "set_current", "current_frame": 12},
        _context(cmds, "maya.animation.time.manage"),
    )
    assert result["status"] == "SUCCESS"
    assert result["output"]["timeline"]["current_frame"] == 12.0
    assert cmds.undo_calls == []


def test_set_current_restores_original_frame_when_post_read_fails():
    class FailingTimelineReadCmds(AnimationCmds):
        def __init__(self):
            super().__init__()
            self.fail_next_query = False

        def currentTime(self, value=None, **kwargs):
            if kwargs.get("query") and self.fail_next_query:
                self.fail_next_query = False
                raise RuntimeError("simulated timeline read failure")
            result = super().currentTime(value, **kwargs)
            if not kwargs.get("query") and float(value) != 1.0:
                self.fail_next_query = True
            return result

    cmds = FailingTimelineReadCmds()
    result = animation_core.manage_time(
        {"action": "set_current", "current_frame": 12},
        _context(cmds, "maya.animation.time.manage"),
    )
    assert result["status"] == "ERROR"
    assert cmds.current == 1.0
    assert cmds.undo_calls == []


def test_keyframe_set_query_delete_and_ambiguous_node_guard():
    cmds = AnimationCmds(); context = _context(cmds, "maya.animation.keyframes.manage")
    set_result = animation_core.manage_keyframes(
        {"action": "set", "nodes": ["|ctrl"], "attributes": ["tx"],
         "time": 12, "value": 3.0, "max_items": 10}, context,
    )
    assert set_result["status"] == "SUCCESS"
    query = animation_core.manage_keyframes(
        {"action": "query", "nodes": ["|ctrl"], "attributes": ["tx"],
         "max_items": 10}, context,
    )
    assert query["output"]["items"][0]["times"] == [1.0, 12.0]
    deleted = animation_core.manage_keyframes(
        {"action": "delete", "nodes": ["|ctrl"], "attributes": ["tx"],
         "start_time": 12, "end_time": 12, "max_items": 10}, context,
    )
    assert deleted["output"]["items"][0]["changed"] == 1
    ambiguous = animation_core.manage_keyframes(
        {"action": "query", "nodes": ["dup"], "attributes": [], "max_items": 10}, context,
    )
    assert ambiguous["status"] == "ERROR"
    assert "ambiguous" in ambiguous["error"]


def test_keyframe_partial_failure_rolls_back_the_chunk():
    class FailingCmds(AnimationCmds):
        def setKeyframe(self, node, **kwargs):
            if node == "|other":
                raise RuntimeError("simulated key failure")
            return super().setKeyframe(node, **kwargs)

    cmds = FailingCmds()
    result = animation_core.manage_keyframes(
        {"action": "set", "nodes": ["|ctrl", "|other"], "attributes": ["tx"],
         "time": 12, "max_items": 10},
        _context(cmds, "maya.animation.keyframes.manage"),
    )
    assert result["status"] == "ERROR"
    assert cmds.undo_count == 1
    assert 12.0 not in cmds.keys["|ctrl"]["tx"]


def test_keyframe_zero_change_rolls_back_the_chunk():
    class ZeroChangeCmds(AnimationCmds):
        def setKeyframe(self, node, **kwargs):
            if node == "|other":
                return 0
            return super().setKeyframe(node, **kwargs)

    cmds = ZeroChangeCmds()
    result = animation_core.manage_keyframes(
        {"action": "set", "nodes": ["|ctrl", "|other"], "attributes": ["tx"],
         "time": 12, "max_items": 10},
        _context(cmds, "maya.animation.keyframes.manage"),
    )
    assert result["status"] == "ERROR"
    assert cmds.undo_count == 1
    assert 12.0 not in cmds.keys["|ctrl"]["tx"]


def test_skin_bind_and_query_compatible_weight_write():
    cmds = SkinCmds()
    bound = skin_core.bind_skin(
        {"action": "bind", "mesh": "|mesh", "joints": ["|j1", "|j2"],
         "max_influences": 2, "bind_method": 0, "name": "faceSkin", "skin_cluster": ""},
        _context(cmds, "maya.rig.skin.bind"),
    )
    assert bound["status"] == "SUCCESS"
    assert bound["output"]["skin_cluster"] == "faceSkin"
    result = skin_core.set_weights(
        {"mesh": "|mesh", "skin_cluster": "faceSkin", "normalization_tolerance": 0.0001,
         "vertices": [{"vertex": 0, "weights": {"|j1": 0.25, "|j2": 0.75}}]},
        _context(cmds, "maya.rig.skin.set_weights"),
    )
    assert result["status"] == "SUCCESS"
    assert result["output"]["rows"][0]["weights"] == {"|j1": 0.25, "|j2": 0.75}


def test_skin_bind_output_feeds_set_weights_when_maya_queries_short_influences():
    class ShortInfluenceCmds(SkinCmds):
        def skinCluster(self, *args, **kwargs):
            result = super().skinCluster(*args, **kwargs)
            if kwargs.get("query") and kwargs.get("influence"):
                return [value.rsplit("|", 1)[-1] for value in result]
            return result

        def skinPercent(self, cluster, component, **kwargs):
            if kwargs.get("query"):
                return super().skinPercent(cluster, component, **kwargs)
            pairs = dict(kwargs["transformValue"])
            vertex = int(component.rsplit("[", 1)[1][:-1])
            self.weights[vertex] = [pairs.get(influence, 0.0) for influence in self.influences]
            self.written = True

    cmds = ShortInfluenceCmds()
    bound = skin_core.bind_skin(
        {"action": "bind", "mesh": "|mesh", "joints": ["|j1", "|j2"],
         "max_influences": 2, "bind_method": 0, "name": "faceSkin", "skin_cluster": ""},
        _context(cmds, "maya.rig.skin.bind"),
    )
    influences = bound["output"]["influences"]
    result = skin_core.set_weights(
        {"mesh": "|mesh", "skin_cluster": "faceSkin", "normalization_tolerance": 0.0001,
         "vertices": [{"vertex": 0, "weights": {
             influences[0]: 0.25, influences[1]: 0.75,
         }}]},
        _context(cmds, "maya.rig.skin.set_weights"),
    )
    assert result["status"] == "SUCCESS"
    assert result["output"]["influences"] == influences

    queried = skin_query.execute(
        {"mesh": "|mesh", "vertices": [0], "influence": "",
         "minimum_weight": 0, "max_vertices": 50},
        _context(cmds, "maya.rig.skin.query_weights"),
    )
    assert queried["status"] == "SUCCESS"
    assert queried["output"]["influences"] == influences
    replayed = skin_core.set_weights(
        {"mesh": "|mesh", "skin_cluster": "faceSkin", "normalization_tolerance": 0.0001,
         "vertices": queried["output"]["rows"]},
        _context(cmds, "maya.rig.skin.set_weights"),
    )
    assert replayed["status"] == "SUCCESS"


def test_skin_bind_default_max_influences_accepts_two_joints():
    cmds = SkinCmds()
    result = skin_core.bind_skin(
        {"action": "bind", "mesh": "|mesh", "joints": ["|j1", "|j2"],
         "max_influences": 4, "bind_method": 0, "name": "", "skin_cluster": ""},
        _context(cmds, "maya.rig.skin.bind"),
    )
    assert result["status"] == "SUCCESS"
    assert result["output"]["max_influences"] == 4


def test_skin_bind_method_three_is_rejected_before_mutation():
    cmds = SkinCmds()
    result = skin_core.bind_skin(
        {"action": "bind", "mesh": "|mesh", "joints": ["|j1", "|j2"],
         "max_influences": 4, "bind_method": 3, "name": "", "skin_cluster": ""},
        _context(cmds, "maya.rig.skin.bind"),
    )
    assert result["status"] == "ERROR"
    assert "requires geomBind" in result["error"]
    assert cmds.history == []
    assert cmds.undo_calls == []


def test_explicit_skin_cluster_must_belong_to_target_mesh():
    cmds = SkinCmds()
    cmds.nodes.update({"faceSkin": "skinCluster", "otherSkin": "skinCluster"})
    cmds.history = ["faceSkin"]
    cmds.influences = ["|j1", "|j2"]
    result = skin_core.bind_skin(
        {"action": "unbind", "mesh": "|mesh", "joints": [], "max_influences": 4,
         "bind_method": 0, "name": "", "skin_cluster": "otherSkin"},
        _context(cmds, "maya.rig.skin.bind"),
    )
    assert result["status"] == "ERROR"
    assert "does not deform the target mesh" in result["error"]
    assert "otherSkin" in cmds.nodes and "faceSkin" in cmds.history


def test_invalid_skin_weight_sum_is_rejected_before_mutation():
    cmds = SkinCmds()
    cmds.nodes["faceSkin"] = "skinCluster"
    cmds.history = ["faceSkin"]
    cmds.influences = ["|j1", "|j2"]
    cmds.weights = {0: [1.0, 0.0], 1: [0.0, 1.0]}
    result = skin_core.set_weights(
        {"mesh": "|mesh", "skin_cluster": "faceSkin", "normalization_tolerance": 0.0001,
         "vertices": [{"vertex": 0, "weights": {"|j1": 0.2, "|j2": 0.2}}]},
        _context(cmds, "maya.rig.skin.set_weights"),
    )
    assert result["status"] == "ERROR"
    assert cmds.weights[0] == [1.0, 0.0]
    assert cmds.undo_calls == []


def test_skin_weight_readback_failure_rolls_back_the_chunk():
    cmds = SkinCmds()
    cmds.nodes["faceSkin"] = "skinCluster"
    cmds.history = ["faceSkin"]
    cmds.influences = ["|j1", "|j2"]
    cmds.weights = {0: [1.0, 0.0], 1: [0.0, 1.0]}
    cmds.corrupt_after_write = True
    result = skin_core.set_weights(
        {"mesh": "|mesh", "skin_cluster": "faceSkin", "normalization_tolerance": 0.0001,
         "vertices": [{"vertex": 0, "weights": {"|j1": 0.25, "|j2": 0.75}}]},
        _context(cmds, "maya.rig.skin.set_weights"),
    )
    assert result["status"] == "ERROR"
    assert cmds.undo_count == 1
    assert cmds.weights[0] == [1.0, 0.0]
