# -*- coding: utf-8 -*-
"""在 Maya 前台打开 v090 评估场景并选中当前推荐候选。"""

import json
import os

import maya.cmds as cmds


SCENE_PATH = r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\test_v090_A_patchSurface_candidates_eval.ma"
RECOMMENDED_MESH = "A_V090_002_strictBlend035"
POSE_ATTRS = [
    "M_Jaw_A_ctrl.rotateX",
    "M_Mouth_A_ctrl.translateY",
    "L_CheekA_A_ctrl.translateY",
    "R_CheekA_A_ctrl.translateY",
    "L_UpCheek_A_ctrl.translateY",
    "R_UpCheek_A_ctrl.translateY",
    "L_UpLid_A_ctrl.translateY",
    "R_UpLid_A_ctrl.translateY",
    "L_LoLid_A_ctrl.translateY",
    "R_LoLid_A_ctrl.translateY",
    "L_UpLip_A_ctrl.translateY",
    "R_UpLip_A_ctrl.translateY",
    "L_LoLip_A_ctrl.translateY",
    "R_LoLip_A_ctrl.translateY",
    "M_Nose_A_ctrl.translateY",
    "M_Chin_A_ctrl.translateY",
]


def _norm_path(path):
    return os.path.normcase(os.path.normpath(path or ""))


def _open_scene_if_needed():
    current = cmds.file(q=True, sn=True) or ""
    if _norm_path(current) != _norm_path(SCENE_PATH):
        cmds.file(SCENE_PATH, open=True, force=True)


def _reset_pose():
    for attr in POSE_ATTRS:
        if not cmds.objExists(attr):
            continue
        try:
            cmds.setAttr(attr, 0.0)
        except Exception:
            pass


def _find_related_sets():
    sets = cmds.ls(type="objectSet") or []
    tokens = ("V090", "STRICT", "BLEND035")
    return sorted([s for s in sets if all(t in s.upper() for t in tokens)])


def main():
    _open_scene_if_needed()
    _reset_pose()

    related_sets = _find_related_sets()
    selection = []
    if cmds.objExists(RECOMMENDED_MESH):
        selection.append(RECOMMENDED_MESH)
    selection.extend([s for s in related_sets if cmds.objExists(s)])
    if selection:
        cmds.select(selection, replace=True)

    result = {
        "scene": cmds.file(q=True, sn=True),
        "recommended_mesh": RECOMMENDED_MESH,
        "recommended_exists": cmds.objExists(RECOMMENDED_MESH),
        "related_sets": related_sets,
        "selected": cmds.ls(sl=True) or [],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


result = main()
