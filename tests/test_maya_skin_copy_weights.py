"""Direct contracts for canonical Maya skin-weight copy and mirror."""

from __future__ import annotations

import copy
import pytest

from cgi_pipeline.capabilities.maya.rig.skin import copy_weights
from cgi_pipeline.capabilities.maya.rig.rig_ops_common import mesh
from cgi_pipeline.contracts import ApiContext


def _context(cmds):
    return ApiContext.from_mapping(
        {"_api_id": "maya.rig.skin.copy_weights", "_api_version": "1.0.0",
         "cmds_module": cmds}, "maya"
    )


class SkinCmds:
    def __init__(self, normalized=True):
        self.nodes = {"|source": "transform", "|source|sourceShape": "mesh",
                      "|target": "transform", "|target|targetShape": "mesh",
                      "|sourceSkin": "skinCluster", "|j1": "joint", "|j2": "joint"}
        self.shapes = {"|source": ["|source|sourceShape"], "|target": ["|target|targetShape"]}
        self.parents = {"|source|sourceShape": "|source", "|target|targetShape": "|target"}
        self.history = {"|source|sourceShape": ["|sourceSkin"], "|target|targetShape": []}
        self.influences = {"|sourceSkin": ["|j1", "|j2"]}
        self.normalized = normalized
        self.copy_kwargs = None
        self.undo_calls = []
        self._snapshot = None

    def ls(self, node=None, **kwargs):
        if isinstance(node, (list, tuple)):
            return [item for value in node for item in (self.ls(value, **kwargs) or [])]
        matches = [node] if node in self.nodes else [
            path for path in self.nodes if path.rsplit("|", 1)[-1] == node
        ]
        if kwargs.get("type"):
            matches = [path for path in matches if self.nodes[path] == kwargs["type"]]
        return matches

    def nodeType(self, node):
        return self.nodes[node]

    def listRelatives(self, node, **kwargs):
        if kwargs.get("parent"):
            return [self.parents[node]] if node in self.parents else []
        if kwargs.get("shapes"):
            return self.shapes.get(node, [])
        return []

    def listHistory(self, shape, **kwargs):
        return self.history[shape]

    def skinCluster(self, *args, **kwargs):
        if kwargs.get("query") and kwargs.get("influence"):
            return self.influences[args[0]]
        node = "|" + kwargs.get("name", "targetSkin")
        self.nodes[node] = "skinCluster"
        self.influences[node] = list(args[:-1])
        self.history["|target|targetShape"] = [node]
        return [node]

    def copySkinWeights(self, **kwargs):
        if kwargs.get("noMirror") and "mirrorMode" in kwargs:
            raise RuntimeError("mirrorMode and noMirror are mutually exclusive")
        self.copy_kwargs = kwargs

    def polyEvaluate(self, shape, **kwargs):
        return 3

    def skinPercent(self, cluster, component, **kwargs):
        return [0.5, 0.5] if self.normalized else [0.2, 0.2]

    def undoInfo(self, **kwargs):
        self.undo_calls.append(kwargs)
        if kwargs.get("openChunk"):
            self._snapshot = copy.deepcopy((self.nodes, self.history, self.influences))

    def undo(self):
        self.undo_calls.append({"undo": True})
        self.nodes, self.history, self.influences = copy.deepcopy(self._snapshot)


def test_copy_omits_mirror_mode_and_mirror_uses_it():
    cmds = SkinCmds()
    copied = copy_weights.copy_weights(
        {"source_mesh": "|source", "target_mesh": "|target",
         "create_missing_target_skin_cluster": True}, _context(cmds),
    )
    assert copied["status"] == "SUCCESS"
    assert len(copied["output"]["verified_vertices"]) == 3
    assert cmds.copy_kwargs["noMirror"] is True
    assert "mirrorMode" not in cmds.copy_kwargs

    repeated = copy_weights.copy_weights(
        {"source_mesh": "|source", "target_mesh": "|target",
         "create_missing_target_skin_cluster": True}, _context(cmds),
    )
    assert repeated["status"] == "SUCCESS"
    assert repeated["output"]["created_target_skin_cluster"] is False

    mirrored = copy_weights.copy_weights(
        {"source_mesh": "|source", "target_mesh": "|target",
         "target_skin_cluster": "|targetSkin", "mirror": True, "mirror_mode": "XZ"},
        _context(cmds),
    )
    assert mirrored["status"] == "SUCCESS"
    assert cmds.copy_kwargs["noMirror"] is False
    assert cmds.copy_kwargs["mirrorMode"] == "XZ"


def test_invalid_contract_preflights_and_bad_readback_rolls_back():
    cmds = SkinCmds()
    invalid = copy_weights.copy_weights(
        {"source_mesh": "|source", "target_mesh": "|target",
         "create_missing_target_skin_cluster": True, "influence_association": ["bad"]},
        _context(cmds),
    )
    assert invalid["status"] == "ERROR" and cmds.undo_calls == []

    cmds = SkinCmds(normalized=False)
    failed = copy_weights.copy_weights(
        {"source_mesh": "|source", "target_mesh": "|target",
         "create_missing_target_skin_cluster": True}, _context(cmds),
    )
    assert failed["status"] == "ERROR" and "|targetSkin" not in cmds.nodes
    assert cmds.undo_calls[-1] == {"undo": True}


@pytest.mark.parametrize('node', ['|source', '|source|sourceShape'])
def test_shared_mesh_accepts_exact_transform_or_shape(node):
    assert mesh(SkinCmds(), node) == ('|source', '|source|sourceShape')


@pytest.mark.parametrize('case', ['missing', 'ambiguous', 'extra_shape', 'orphan', 'empty'])
def test_shared_mesh_preserves_strict_failures(case):
    cmds, node = SkinCmds(), '|source'
    if case == 'missing':
        node = '|missing'
    elif case == 'ambiguous':
        cmds.nodes['|other|source'] = 'transform'
        node = 'source'
    elif case == 'extra_shape':
        cmds.nodes['|source|extraShape'] = 'mesh'
        cmds.shapes['|source'].append('|source|extraShape')
    elif case == 'orphan':
        node = '|source|sourceShape'
        cmds.parents.pop(node)
    else:
        node = None
    with pytest.raises(ValueError):
        mesh(cmds, node)
