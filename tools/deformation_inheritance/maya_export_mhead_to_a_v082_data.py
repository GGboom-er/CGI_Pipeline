"""Export clean v082 geometry and source skin data from the foreground Maya scene."""

from __future__ import annotations

import json
import os
from pathlib import Path

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.cmds as cmds
import numpy as np


SOURCE = "M_Head_base"
TARGET = "A"
PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
OUT_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
OUT_NPZ = OUT_DIR / "v082_clean_scene_data.npz"
OUT_JSON = OUT_DIR / "v082_clean_scene_data_summary.json"


def _long(name: str) -> str:
    found = cmds.ls(name, long=True) or []
    if not found:
        raise RuntimeError("找不到节点: %s" % name)
    return found[0]


def _shape(transform: str) -> str:
    shapes = cmds.listRelatives(transform, shapes=True, noIntermediate=True, fullPath=True) or []
    if not shapes:
        raise RuntimeError("节点没有有效 mesh shape: %s" % transform)
    return shapes[0]


def _dag(node: str) -> om2.MDagPath:
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def _node(node: str) -> om2.MObject:
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDependNode(0)


def _skin(transform: str) -> str | None:
    history = cmds.listHistory(transform, pruneDagObjects=True) or []
    skins = []
    for node in history:
        try:
            if cmds.nodeType(node) == "skinCluster":
                skins.append(node)
        except Exception:
            pass
    return sorted(set(skins))[0] if skins else None


def _mesh_data(transform: str) -> dict:
    shape = _shape(transform)
    fn = om2.MFnMesh(_dag(shape))
    points = np.asarray([[p.x, p.y, p.z] for p in fn.getPoints(om2.MSpace.kWorld)], dtype=np.float64)
    counts, flat = fn.getVertices()
    counts_np = np.asarray(counts, dtype=np.int32)
    flat_np = np.asarray(flat, dtype=np.int32)
    offsets = np.zeros(counts_np.shape[0] + 1, dtype=np.int64)
    offsets[1:] = np.cumsum(counts_np, dtype=np.int64)
    return {
        "transform": transform,
        "shape": shape,
        "points": points,
        "face_counts": counts_np,
        "face_offsets": offsets,
        "face_vertices": flat_np,
        "bbox": np.asarray(cmds.exactWorldBoundingBox(transform), dtype=np.float64),
    }


def _all_vertex_component(vertex_count: int) -> om2.MObject:
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.addElements(om2.MIntArray(list(range(vertex_count))))
    return comp


def _skin_weights(transform: str) -> tuple[str, list[str], np.ndarray]:
    skin = _skin(transform)
    if not skin:
        raise RuntimeError("source 缺少 skinCluster: %s" % transform)
    fn = oma2.MFnSkinCluster(_node(skin))
    influences = [path.fullPathName() for path in fn.influenceObjects()]
    shape = _shape(transform)
    dag = _dag(shape)
    vertex_count = int(cmds.polyEvaluate(transform, vertex=True))
    comp = _all_vertex_component(vertex_count)
    weights, influence_count = fn.getWeights(dag, comp)
    arr = np.asarray(weights, dtype=np.float64).reshape((vertex_count, int(influence_count)))
    return skin, influences, arr


def _control_values() -> dict:
    controls = [
        "M_Jaw_A_ctrl",
        "R_CheekA_A_ctrl",
        "L_CheekA_A_ctrl",
        "R_UpLid_A_ctrl",
        "L_UpLid_A_ctrl",
        "R_LoLid_A_ctrl",
        "L_LoLid_A_ctrl",
    ]
    data = {}
    for ctrl in controls:
        if not cmds.objExists(ctrl):
            continue
        attrs = {}
        for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ"):
            plug = ctrl + "." + attr
            if cmds.objExists(plug):
                try:
                    attrs[attr] = float(cmds.getAttr(plug))
                except Exception:
                    pass
        data[ctrl] = attrs
    return data


def _execute() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = _long(SOURCE)
    target = _long(TARGET)
    source_mesh = _mesh_data(source)
    target_mesh = _mesh_data(target)
    skin, influences, weights = _skin_weights(source)

    payload = {
        "source_points": source_mesh["points"],
        "source_face_counts": source_mesh["face_counts"],
        "source_face_offsets": source_mesh["face_offsets"],
        "source_face_vertices": source_mesh["face_vertices"],
        "source_bbox": source_mesh["bbox"],
        "target_points": target_mesh["points"],
        "target_face_counts": target_mesh["face_counts"],
        "target_face_offsets": target_mesh["face_offsets"],
        "target_face_vertices": target_mesh["face_vertices"],
        "target_bbox": target_mesh["bbox"],
        "source_weights": weights,
        "influence_names": np.asarray(influences, dtype=object),
    }
    np.savez_compressed(str(OUT_NPZ), **payload)

    summary = {
        "status": "SUCCESS",
        "scene": cmds.file(q=True, sceneName=True),
        "output_npz": str(OUT_NPZ),
        "source": {
            "transform": source,
            "shape": source_mesh["shape"],
            "vertex_count": int(source_mesh["points"].shape[0]),
            "face_count": int(source_mesh["face_counts"].shape[0]),
            "skinCluster": skin,
            "bbox": source_mesh["bbox"].tolist(),
        },
        "target": {
            "transform": target,
            "shape": target_mesh["shape"],
            "vertex_count": int(target_mesh["points"].shape[0]),
            "face_count": int(target_mesh["face_counts"].shape[0]),
            "skinCluster": _skin(target),
            "bbox": target_mesh["bbox"].tolist(),
        },
        "influence_count": int(len(influences)),
        "source_weight_row_sum_error": float(np.max(np.abs(weights.sum(axis=1) - 1.0))),
        "control_values": _control_values(),
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


result = _execute()
