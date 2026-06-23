"""Create Maya sets for v088 visibility correspondence probe.

只创建诊断选择集，不改权重、不改几何。
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

import maya.cmds as cmds
import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
INPUT_NPZ = INFO_DIR / "v088_visibility_correspondence_probe.npz"
REPORT_JSON = INFO_DIR / "v088_maya_visibility_sets.json"

MESH_CANDIDATES = [
    "A",
    "A_V087_001_baseHybrid",
    "A_V087_002_rawRT_DIAGNOSTIC",
    "A_V087_003_guardStrictRT",
    "A_V087_004_guardBalancedRT",
    "A_V087_005_guardBalancedRTS",
]

SET_FLAGS = {
    "visibilityRisk": "visibility_risk",
    "sourceFirstNotBest": "source_first_not_best",
    "targetSegmentBlocked": "target_segment_blocked",
    "targetNormalLayerHit": "target_normal_layer_hit",
    "targetNormalFrontLayerHit": "target_normal_front_layer_hit",
    "actualBaseHigh015": "base_high_015",
    "candidateRegressionAny": "candidate_regression_any",
}


def _make_set(mesh: str, set_name: str, ids: np.ndarray, limit: int | None = None) -> dict:
    if cmds.objExists(set_name):
        cmds.delete(set_name)
    clean = sorted(set(int(x) for x in ids.tolist()))
    total = len(clean)
    if limit is not None:
        clean = clean[:limit]
    if not clean:
        created = cmds.sets(empty=True, name=set_name)
        return {"set": created, "count": total, "created_count": 0}
    comps = ["%s.vtx[%d]" % (mesh, idx) for idx in clean]
    created = cmds.sets(comps, name=set_name)
    return {"set": created, "count": total, "created_count": len(clean)}


def _execute() -> dict:
    if not INPUT_NPZ.exists():
        raise RuntimeError("缺少 v088 probe npz: %s" % INPUT_NPZ)
    data = np.load(str(INPUT_NPZ), allow_pickle=True)
    existing_meshes = [mesh for mesh in MESH_CANDIDATES if cmds.objExists(mesh)]
    if not existing_meshes:
        raise RuntimeError("场景中找不到 A 或 v087 对照 mesh")

    sets = {}
    default_select = []
    for mesh in existing_meshes:
        mesh_sets = {}
        for label, key in SET_FLAGS.items():
            flag = np.asarray(data[key], dtype=bool)
            ids = np.where(flag)[0].astype(np.int32)
            set_name = "CDFDIAG_V088_%s_%s_SET" % (mesh, label)
            mesh_sets[label] = _make_set(mesh, set_name, ids)
            if mesh == existing_meshes[0] and label in {"visibilityRisk", "targetNormalFrontLayerHit", "candidateRegressionAny"}:
                default_select.append(mesh_sets[label]["set"])
        sets[mesh] = mesh_sets

    if default_select:
        cmds.select(default_select, r=True)

    result = {
        "status": "SUCCESS",
        "scene": cmds.file(q=True, sceneName=True),
        "input_npz": str(INPUT_NPZ),
        "existing_meshes": existing_meshes,
        "sets": sets,
        "default_selected_sets": default_select,
    }
    REPORT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


try:
    result = _execute()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
    REPORT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
