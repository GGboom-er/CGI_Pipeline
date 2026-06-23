"""在 v087 对比场景中创建低置信和候选改动点诊断 set。"""

from __future__ import annotations

import json
from pathlib import Path
import traceback

import maya.cmds as cmds
import numpy as np


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
INPUT_NPZ = INFO_DIR / "v087_all_influence_input.npz"
CANDIDATE_NPZ = INFO_DIR / "v087_all_influence_candidates.npz"
REPORT_JSON = INFO_DIR / "v087_maya_diagnostic_sets.json"

TARGET = "A"
BASE_KEY = "weights__v087_base_hybrid_m0010"
VARIANT_MESHES = {
    "A_V087_003_guardStrictRT": "weights__v087_rt_guard_strict",
    "A_V087_004_guardBalancedRT": "weights__v087_rt_guard_balanced",
    "A_V087_005_guardBalancedRTS": "weights__v087_rts_guard_balanced",
}


def _delete_set(name: str) -> None:
    if cmds.objExists(name):
        cmds.delete(name)


def _make_vertex_set(mesh: str, indices: np.ndarray, name: str, max_items: int | None = None) -> dict:
    ids = np.asarray(indices, dtype=np.int64)
    if max_items is not None and ids.size > max_items:
        ids = ids[:max_items]
    _delete_set(name)
    if ids.size == 0:
        cmds.sets(empty=True, name=name)
    else:
        comps = ["%s.vtx[%d]" % (mesh, int(i)) for i in ids.tolist()]
        cmds.sets(comps, name=name)
    return {"set": name, "mesh": mesh, "count": int(np.asarray(indices).size), "created_count": int(ids.size)}


def _execute() -> dict:
    input_data = np.load(str(INPUT_NPZ), allow_pickle=True)
    cand = np.load(str(CANDIDATE_NPZ), allow_pickle=True)
    low_confidence = np.asarray(input_data["low_confidence"], dtype=bool)
    unsupported = np.asarray(input_data["unsupported"], dtype=bool)
    high_confidence = np.asarray(input_data["high_confidence"], dtype=bool)
    topology_discontinuity = np.asarray(input_data["topology_discontinuity"], dtype=bool)
    semantic_ambiguous = np.asarray(input_data["semantic_ambiguous"], dtype=bool)

    report = {"status": "SUCCESS", "sets": []}
    report["sets"].append(_make_vertex_set(TARGET, np.where(low_confidence)[0], "CDFDIAG_V087_A_lowConfidence_SET"))
    report["sets"].append(_make_vertex_set(TARGET, np.where(unsupported)[0], "CDFDIAG_V087_A_unsupported_SET"))
    report["sets"].append(_make_vertex_set(TARGET, np.where(high_confidence)[0], "CDFDIAG_V087_A_highConfidence_SET"))
    report["sets"].append(_make_vertex_set(TARGET, np.where(topology_discontinuity)[0], "CDFDIAG_V087_A_topologyDiscontinuity_SET"))
    report["sets"].append(_make_vertex_set(TARGET, np.where(semantic_ambiguous)[0], "CDFDIAG_V087_A_semanticAmbiguous_SET"))

    base = np.asarray(cand[BASE_KEY], dtype=np.float64)
    for mesh, key in VARIANT_MESHES.items():
        weights = np.asarray(cand[key], dtype=np.float64)
        changed = np.where(np.sum(np.abs(weights - base), axis=1) > 1e-8)[0]
        if cmds.objExists(mesh):
            report["sets"].append(_make_vertex_set(mesh, changed, "CDFDIAG_V087_%s_changed_SET" % mesh))
        accept_key = "accept__" + key.replace("weights__", "")
        if accept_key in cand.files and cmds.objExists(mesh):
            accepted = np.where(np.asarray(cand[accept_key], dtype=bool))[0]
            report["sets"].append(_make_vertex_set(mesh, accepted, "CDFDIAG_V087_%s_accepted_SET" % mesh))

    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


try:
    result = _execute()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
