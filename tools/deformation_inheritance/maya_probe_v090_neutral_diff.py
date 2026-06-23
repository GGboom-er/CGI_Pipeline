"""探测 v090 候选和 v089b base 的 neutral/权重差异。"""

from __future__ import annotations

import json
from pathlib import Path
import traceback

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.cmds as cmds
import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
SCENE = PROJECT_DIR / "test_v090_A_patchSurface_candidates.ma"
BASE = "A_V089B_001_baseHybrid"
PROBES = [
    "A_V090_001_strictDirect",
    "A_V090_002_strictBlend035",
    "A_V090_003_balancedDirect",
    "A_V090_005_broadDirect",
]


def _shape(mesh: str) -> str:
    shapes = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True) or []
    meshes = [shape for shape in shapes if cmds.nodeType(shape) == "mesh"]
    if not meshes:
        raise RuntimeError("找不到 mesh: %s" % mesh)
    return meshes[0]


def _dag(node: str) -> om2.MDagPath:
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def _node(node: str) -> om2.MObject:
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDependNode(0)


def _points(mesh: str) -> np.ndarray:
    pts = om2.MFnMesh(_dag(_shape(mesh))).getPoints(om2.MSpace.kWorld)
    return np.asarray([[p.x, p.y, p.z] for p in pts], dtype=np.float64)


def _skin(mesh: str) -> str:
    hist = cmds.listHistory(_shape(mesh), pruneDagObjects=True) or []
    skins = cmds.ls(hist, type="skinCluster") or []
    if not skins:
        raise RuntimeError("无 skinCluster: %s" % mesh)
    return skins[0]


def _weights(mesh: str) -> np.ndarray:
    shape = _shape(mesh)
    skin = _skin(mesh)
    fn = oma2.MFnSkinCluster(_node(skin))
    count = int(cmds.polyEvaluate(mesh, vertex=True))
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.addElements(list(range(count)))
    flat, influence_count = fn.getWeights(_dag(shape), comp)
    return np.asarray(flat, dtype=np.float64).reshape(count, int(influence_count))


def _execute() -> dict:
    cmds.file(str(SCENE), open=True, force=True)
    base_pts = _points(BASE)
    base_w = _weights(BASE)
    out = {"scene": str(SCENE), "base": BASE, "probes": {}}
    for mesh in PROBES:
        pts = _points(mesh)
        # translateX offset should be the mean X difference; remove it before shape compare.
        offset = np.mean(pts - base_pts, axis=0)
        pts_aligned = pts - offset
        point_diff = np.linalg.norm(pts_aligned - base_pts, axis=1)
        w = _weights(mesh)
        weight_diff = np.abs(w - base_w).sum(axis=1)
        out["probes"][mesh] = {
            "offset": offset.tolist(),
            "point_diff_max_after_offset": float(np.max(point_diff)),
            "point_diff_p95_after_offset": float(np.percentile(point_diff, 95)),
            "weight_diff_nonzero_gt_1e_6": int(np.count_nonzero(weight_diff > 1e-6)),
            "weight_diff_max": float(np.max(weight_diff)),
            "weight_diff_p95": float(np.percentile(weight_diff, 95)),
        }
    return out


try:
    result = _execute()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
