import json
from pathlib import Path

import maya.api.OpenMaya as om2
import maya.cmds as cmds
import numpy as np


INFO_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\.info\a_weight_transfer_v082_reboot"
)
SOURCE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
VALIDATION_PATH = INFO_DIR / "param_sweep" / "v082p00_distance_k64_correspondence_validation.npz"
OUT_PATH = INFO_DIR / "v086_alpha_pose_vectors.npz"
SUMMARY_PATH = INFO_DIR / "v086_alpha_pose_vectors_summary.json"

BASE_KEY = "hybrid_m0010_cc3"
MOTION_KEY = "v084_inverse_motion_accepted"
BASE_MESH = "A_V084TEST_000_baseHybrid"
MOTION_MESH = "A_V084TEST_003_inverseMotion"

POSES = [
    ("jaw_rx_5", {"M_Jaw_A_ctrl.rotateX": 5.0}),
    ("jaw_rx_10", {"M_Jaw_A_ctrl.rotateX": 10.0}),
    ("jaw_rx_15", {"M_Jaw_A_ctrl.rotateX": 15.0}),
    ("jaw_rx_20", {"M_Jaw_A_ctrl.rotateX": 20.0}),
    ("jaw_rx_25", {"M_Jaw_A_ctrl.rotateX": 25.0}),
    ("jaw_rx_30", {"M_Jaw_A_ctrl.rotateX": 30.0}),
    ("r_cheekA_ty_1", {"R_CheekA_A_ctrl.translateY": 1.0}),
    ("l_cheekA_ty_1", {"L_CheekA_A_ctrl.translateY": 1.0}),
    ("r_upLidMid_ty_05", {"R_UpLidMid_A_ctrl.translateY": 0.5}),
    ("r_loLidMid_ty_n05", {"R_LoLidMid_A_ctrl.translateY": -0.5}),
    ("l_upLidMid_ty_05", {"L_UpLidMid_A_ctrl.translateY": 0.5}),
    ("l_loLidMid_ty_n05", {"L_LoLidMid_A_ctrl.translateY": -0.5}),
]


def _shape(mesh):
    shapes = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True) or []
    if not shapes:
        shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or []
    if not shapes:
        raise RuntimeError("No mesh shape found under %s" % mesh)
    return shapes[0]


def _dag_path(node):
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def _get_points(mesh):
    fn = om2.MFnMesh(_dag_path(_shape(mesh)))
    pts = fn.getPoints(om2.MSpace.kWorld)
    return np.asarray([[p.x, p.y, p.z] for p in pts], dtype=np.float64)


def _set_attr_safe(attr, value):
    if not cmds.objExists(attr):
        return False
    try:
        if cmds.getAttr(attr, settable=True):
            cmds.setAttr(attr, float(value))
            return True
    except Exception:
        return False
    return False


def _all_pose_attrs():
    attrs = set()
    for _, pose in POSES:
        attrs.update(pose.keys())
    return sorted(attrs)


def _capture_attrs(attrs):
    values = {}
    for attr in attrs:
        if cmds.objExists(attr):
            try:
                values[attr] = float(cmds.getAttr(attr))
            except Exception:
                pass
    return values


def _restore_attrs(values):
    for attr, value in values.items():
        _set_attr_safe(attr, value)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)


def _reset_attrs(attrs):
    active = []
    for attr in attrs:
        if _set_attr_safe(attr, 0.0):
            active.append(attr)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return active


def _apply_pose(pose):
    applied = {}
    for attr, value in pose.items():
        if _set_attr_safe(attr, value):
            applied[attr] = value
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return applied


def _expected_points(val, source_points, ids):
    source_tris = val["source_tris"].astype(np.int32)
    best_tri = val["best_tri"].astype(np.int32)
    best_bary = val["best_bary"].astype(np.float64)
    tris = source_tris[best_tri[ids]]
    bary = best_bary[ids]
    return (
        bary[:, 0:1] * source_points[tris[:, 0]]
        + bary[:, 1:2] * source_points[tris[:, 1]]
        + bary[:, 2:3] * source_points[tris[:, 2]]
    )


def _stat(values):
    values = np.asarray(values)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"count": 0}
    return {
        "count": int(values.size),
        "mean": float(np.mean(values)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
    }


def main():
    for mesh in ("M_Head_base", BASE_MESH, MOTION_MESH):
        if not cmds.objExists(mesh):
            raise RuntimeError("Required mesh missing: %s. Run v084 apply script first." % mesh)

    val = np.load(str(VALIDATION_PATH), allow_pickle=True)
    unsupported = val["unsupported"].astype(bool)
    supported_ids = np.where(~unsupported)[0].astype(np.int32)

    attrs = _all_pose_attrs()
    old_values = _capture_attrs(attrs)
    out = {"supported_ids": supported_ids}
    pose_summaries = {}

    try:
        _reset_attrs(attrs)
        source_neutral = _get_points("M_Head_base")
        base_neutral = _get_points(BASE_MESH)
        motion_neutral = _get_points(MOTION_MESH)
        expected_neutral = _expected_points(val, source_neutral, supported_ids)

        for pose_name, pose in POSES:
            _reset_attrs(attrs)
            applied = _apply_pose(pose)
            if not applied:
                continue

            source_pose = _get_points("M_Head_base")
            base_pose = _get_points(BASE_MESH)
            motion_pose = _get_points(MOTION_MESH)
            expected_delta = _expected_points(val, source_pose, supported_ids) - expected_neutral
            base_vec = (base_pose[supported_ids] - base_neutral[supported_ids]) - expected_delta
            motion_vec = (motion_pose[supported_ids] - motion_neutral[supported_ids]) - expected_delta

            out[pose_name + "__" + BASE_KEY + "__motion_error_vec"] = base_vec.astype(np.float32)
            out[pose_name + "__" + MOTION_KEY + "__motion_error_vec"] = motion_vec.astype(np.float32)
            pose_summaries[pose_name] = {
                "applied": applied,
                "base": _stat(np.linalg.norm(base_vec, axis=1)),
                "motion": _stat(np.linalg.norm(motion_vec, axis=1)),
            }

        np.savez_compressed(str(OUT_PATH), **out)
        summary = {
            "status": "SUCCESS",
            "scene": cmds.file(q=True, sn=True),
            "output_path": str(OUT_PATH),
            "supported_count": int(len(supported_ids)),
            "base_key": BASE_KEY,
            "motion_key": MOTION_KEY,
            "base_mesh": BASE_MESH,
            "motion_mesh": MOTION_MESH,
            "poses": pose_summaries,
        }
        SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        _restore_attrs(old_values)

    return summary


result = main()
