"""Bounded native-LBS input capture and synthetic bind-skeleton pose sampling."""
from __future__ import annotations

import numpy as np

from . import ng_engine


def _matrix(value):
    return np.asarray(value, dtype=float).reshape((4, 4))


def preflight(snapshot, options, cmds):
    name = snapshot["name"]
    if snapshot["attrs"]["skinningMethod"] != 0:
        raise ValueError(name + ": dair_inspired v1 supports native LBS skinningMethod=0")
    if cmds.attributeQuery("relativeSpaceMode", node=name, exists=True):
        if cmds.getAttr(name + ".relativeSpaceMode") != 0:
            raise ValueError(name + ": dair_inspired v1 requires standard relativeSpaceMode=0")
    if cmds.getAttr(name + ".nodeState") != 0:
        raise ValueError(name + ": skin nodeState must be Normal")
    if cmds.listConnections(name + ".weightList", source=True, destination=False, plugs=True):
        raise ValueError(name + ": driven skin weights are unsupported")
    related = cmds.listConnections(name, source=True, destination=True) or []
    if any(cmds.nodeType(node).startswith("ngst2") for node in related):
        raise ValueError(name + ": existing ng layer data must be baked before native weight writes")
    if options["mode"] != "apply":
        return
    if cmds.referenceQuery(name, isNodeReferenced=True) or cmds.lockNode(name, query=True, lock=True)[0]:
        raise ValueError(name + ": skin must be editable in the sandbox")
    if cmds.getAttr(name + ".weightList", lock=True):
        raise ValueError(name + ": locked weightList is unsupported")
    for attr in ("maxInfluences", "maintainMaxInfluences"):
        if not cmds.getAttr(name + "." + attr, settable=True):
            raise ValueError(name + "." + attr + ": not editable")
    for info in snapshot["influences"]:
        plug = info["name"] + ".liw"
        if info["locked"] and not cmds.getAttr(plug, settable=True):
            raise ValueError(plug + ": influence editing lock cannot be temporarily released")


def inspect_input(snapshot, cmds, om, oma):
    """Check input topology/membership without reading points or edge arrays."""
    name = snapshot["name"]
    indices = cmds.deformer(name, query=True, geometryIndices=True) or []
    if len(indices) != 1:
        raise ValueError(name + ": exactly one native geometry index is required")
    index = int(indices[0])
    selection = om.MSelectionList()
    selection.add(name + ".input[{}].inputGeometry".format(index))
    mesh = om.MFnMesh(selection.getPlug(0).asMObject())
    count = mesh.numVertices
    if count != snapshot["count"]:
        raise ValueError(name + ": skin input geometry must match the output topology")
    selection = om.MSelectionList()
    selection.add(name)
    skin = oma.MFnSkinCluster(selection.getDependNode(0))
    component = skin.getComponentAtIndex(index)
    if component.isNull() or not component.hasFn(om.MFn.kMeshVertComponent):
        raise ValueError(name + ": native whole-mesh membership could not be verified")
    membership = om.MFnSingleIndexedComponent(component)
    full = (membership.getCompleteData() == count if membership.isComplete else
            np.array_equal(np.sort(np.asarray(membership.getElements())), np.arange(count)))
    if not full:
        raise ValueError(name + ": partial skin membership is unsupported")
    matrix = _matrix(snapshot["geom_matrix"])
    if not np.isfinite(matrix).all() or not np.allclose(matrix[:, 3], [0, 0, 0, 1]):
        raise ValueError(name + ": skin geomMatrix must be a finite affine transform")
    return mesh, matrix


def input_mesh(snapshot, cmds, om, oma):
    mesh, matrix = inspect_input(snapshot, cmds, om, oma)
    points = np.asarray(mesh.getPoints(om.MSpace.kObject), dtype=float)[:, :3].copy()
    if not np.isfinite(points).all():
        raise ValueError(snapshot["name"] + ": finite skin input vertices are required")
    world = np.column_stack((points, np.ones(len(points)))) @ matrix
    if not np.allclose(world[:, 3], 1) or not np.isfinite(world).all():
        raise ValueError(snapshot["name"] + ": skin geomMatrix must be a finite affine transform")
    counts, vertices = mesh.getVertices()
    counts = np.asarray(counts, dtype=np.int64)
    vertices = np.asarray(vertices, dtype=np.int64)
    if not len(vertices):
        return world[:, :3], np.empty((0, 2), dtype=np.int64)
    ends = np.cumsum(counts) - 1
    starts = np.r_[0, ends[:-1] + 1]
    successors = np.roll(vertices, -1)
    successors[ends] = vertices[starts]
    edges = np.sort(np.column_stack((vertices, successors)), axis=1)
    edges = np.unique(edges[edges[:, 0] != edges[:, 1]], axis=0)
    return world[:, :3], edges


def sample_pivots(snapshot, cmds):
    """Influence bind matrices anchor ancestor pivots through current DAG locals.

This is a bind-oriented independent sampling policy, not animation coverage.
Uninfluenced ancestors use their current relative hierarchy offset; use neutral
Rig Master input for a consistent bind-skeleton interpretation.
"""
    pivots = {}
    for info in sorted(snapshot["influences"], key=lambda item: item["name"]):
        path = info["name"]
        bind_world = np.linalg.inv(_matrix(info["bind_matrix"]))
        pivots[path] = bind_world[3, :3]
        current_inverse = np.linalg.inv(_matrix(info["world_matrix"]))
        parent = path.rsplit("|", 1)[0]
        while parent:
            if parent not in pivots and cmds.nodeType(parent) == "joint":
                current_parent = _matrix(cmds.getAttr(parent + ".worldMatrix[0]"))
                anchored = current_parent @ current_inverse @ bind_world
                pivots[parent] = anchored[3, :3]
            parent = parent.rsplit("|", 1)[0]
    return pivots


def verify(before, after, expected, options, cmds, restored=False):
    ng_engine._check_metadata(before, after, cmds, restored=restored)
    if restored and before["influences"] != after["influences"]:
        raise RuntimeError("rollback did not restore influence identities and mapping")
    rows = list(ng_engine._aligned_rows(before, after))
    actual = np.asarray([row[1] for row in rows])
    if not np.isfinite(actual).all() or (not restored and np.any(actual < 0)):
        raise RuntimeError("native skin write returned invalid weights")
    if restored:
        if not np.array_equal(actual.ravel(), np.asarray(before["weights"])):
            raise RuntimeError("native undo did not exactly restore original stored weights")
        return 0.0
    if np.any(np.count_nonzero(actual, axis=1) > options["max_influences"]):
        raise RuntimeError("native skin write violated the per-vertex influence cap")
    totals = actual.sum(axis=1)
    if np.any(totals <= 0) or (before["changed_vertices"] and not np.allclose(totals, 1, atol=1e-7, rtol=0)):
        raise RuntimeError("native skin write violated sum-one normalization")
    effective = actual / totals[:, None]
    if np.any((effective > 0) & (effective <= options["threshold"])):
        raise RuntimeError("native skin write retained a positive effective weight at or below threshold")
    error = float(np.max(np.abs(effective - expected)))
    if error > 1e-7:
        raise RuntimeError("native skin write differs from the solved weights")
    return error
