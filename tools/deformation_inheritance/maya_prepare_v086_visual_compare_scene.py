import json
from pathlib import Path

import maya.cmds as cmds


PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
REPORT_PATH = INFO_DIR / "v086_visual_compare_summary.json"
MD_PATH = INFO_DIR / "v086_visual_compare_summary.md"
OUT_PATH = PROJECT_DIR / "test_v086_A_alphaGate_compare.ma"

CANDIDATES = [
    ("hybrid_m0010_cc3", "A_V086TEST_000_baseHybrid", -48.0, "base hybrid", "p95max 0.015859 / reg 0"),
    ("v084_inverse_motion_accepted", "A_V086TEST_001_v084Motion", -24.0, "v084 best jaw", "p95max 0.005378 / reg 494"),
    ("v085_inverse_motion_multiposeGated", "A_V086TEST_002_v085HardGate", 0.0, "v085 hard safe", "p95max 0.011309 / reg 0"),
    ("v086_alphaGate_safe005", "A_V086TEST_003_alphaSafe005", 24.0, "v086 safe005", "p95max 0.009740 / reg 0"),
    ("v086_alphaGate_strict003", "A_V086TEST_004_alphaStrict003", 48.0, "v086 strict003", "p95max 0.010283 / reg 0"),
    ("v086_alphaGate_balanced", "A_V086TEST_005_alphaBalanced", 72.0, "v086 balanced", "p95max 0.009924 / reg 0"),
]

METRICS = {
    "hybrid_m0010_cc3": {
        "jaw25_p95": 0.013281,
        "jaw30_p95": 0.015859,
        "supported_p95_max": 0.015859,
        "supported_p95_mean": None,
        "regression_count": 0,
        "role": "映射型基线",
    },
    "v084_inverse_motion_accepted": {
        "jaw25_p95": 0.004658,
        "jaw30_p95": 0.005378,
        "supported_p95_max": 0.005378,
        "supported_p95_mean": 0.001739,
        "regression_count": 494,
        "role": "Jaw 最强，但 Cheek 回归",
    },
    "v085_inverse_motion_multiposeGated": {
        "jaw25_p95": 0.009551,
        "jaw30_p95": 0.011309,
        "supported_p95_max": 0.011309,
        "supported_p95_mean": 0.003373,
        "regression_count": 0,
        "role": "整行回退安全对照",
    },
    "v086_alphaGate_safe005": {
        "jaw25_p95": 0.008297,
        "jaw30_p95": 0.009740,
        "supported_p95_max": 0.009740,
        "supported_p95_mean": 0.002961,
        "regression_count": 0,
        "role": "当前推荐人工复验",
    },
    "v086_alphaGate_strict003": {
        "jaw25_p95": 0.008713,
        "jaw30_p95": 0.010283,
        "supported_p95_max": 0.010283,
        "supported_p95_mean": 0.003102,
        "regression_count": 0,
        "role": "更保守",
    },
    "v086_alphaGate_balanced": {
        "jaw25_p95": 0.008380,
        "jaw30_p95": 0.009924,
        "supported_p95_max": 0.009924,
        "supported_p95_mean": 0.002992,
        "regression_count": 0,
        "role": "折中候选",
    },
}


def _set_attr_safe(attr, value):
    if cmds.objExists(attr):
        try:
            if cmds.getAttr(attr, settable=True):
                cmds.setAttr(attr, float(value))
                return True
        except Exception:
            return False
    return False


def _reset_and_set_jaw25():
    attrs = [
        "L_CheekA_A_ctrl.translateY",
        "L_LoLidMid_A_ctrl.translateY",
        "L_UpLidMid_A_ctrl.translateY",
        "M_Jaw_A_ctrl.rotateX",
        "R_CheekA_A_ctrl.translateY",
        "R_LoLidMid_A_ctrl.translateY",
        "R_UpLidMid_A_ctrl.translateY",
    ]
    reset = []
    for attr in attrs:
        if _set_attr_safe(attr, 0.0):
            reset.append(attr)
    jaw_set = _set_attr_safe("M_Jaw_A_ctrl.rotateX", 25.0)
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)
    return reset, jaw_set


def _bbox_center(mesh):
    bbox = cmds.exactWorldBoundingBox(mesh)
    return (
        (bbox[0] + bbox[3]) * 0.5,
        (bbox[1] + bbox[4]) * 0.5,
        (bbox[2] + bbox[5]) * 0.5,
        bbox,
    )


def _ensure_group(name):
    if cmds.objExists(name):
        cmds.delete(name)
    return cmds.group(empty=True, name=name)


def _make_label(text, mesh, parent):
    cx, _, cz, bbox = _bbox_center(mesh)
    curves = cmds.textCurves(font="Arial", text=text, name=mesh + "_labelText")
    root = curves[0] if isinstance(curves, (list, tuple)) else curves
    root = cmds.rename(root, mesh + "_LABEL")
    cmds.setAttr(root + ".translateX", float(cx - 7.0))
    cmds.setAttr(root + ".translateY", float(bbox[4] + 4.0))
    cmds.setAttr(root + ".translateZ", float(cz + 10.0))
    cmds.setAttr(root + ".scaleX", 0.7)
    cmds.setAttr(root + ".scaleY", 0.7)
    cmds.setAttr(root + ".scaleZ", 0.7)
    cmds.parent(root, parent)
    return root


def _make_set(name, members):
    if cmds.objExists(name):
        cmds.delete(name)
    return cmds.sets(members, name=name)


def _write_reports(scene_path):
    rows = []
    for key, mesh, _, label, note in CANDIDATES:
        item = {"key": key, "mesh": mesh, "label": label, "note": note}
        item.update(METRICS[key])
        rows.append(item)

    payload = {
        "status": "SUCCESS",
        "scene": scene_path,
        "recommended": "A_V086TEST_003_alphaSafe005",
        "pose": {"M_Jaw_A_ctrl.rotateX": 25.0},
        "rows": rows,
        "rule": "先看 v086 safe005；若视觉仍有局部膨胀或面片噪声，下一步做 patch-level surface objective。",
    }
    REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# v086 A 权重复刻对比",
        "",
        "默认姿态：`M_Jaw_A_ctrl.rotateX = 25`。",
        "",
        "| 候选 | Mesh | Jaw25 p95 | Jaw30 p95 | 12姿态 p95 max | 回归点 | 说明 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        mean = row["supported_p95_max"]
        lines.append(
            "| {key} | {mesh} | {jaw25_p95:.6f} | {jaw30_p95:.6f} | {max_p95:.6f} | {regression_count} | {role} |".format(
                key=row["key"],
                mesh=row["mesh"],
                jaw25_p95=row["jaw25_p95"],
                jaw30_p95=row["jaw30_p95"],
                max_p95=mean,
                regression_count=row["regression_count"],
                role=row["role"],
            )
        )
    lines.extend(
        [
            "",
            "推荐先人工看：`A_V086TEST_003_alphaSafe005`。",
            "",
            "判断：v084 Jaw 最强但 Cheek 回归；v085 安全但过保守；v086 safe005 是当前安全收益折中。",
        ]
    )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main():
    missing = [mesh for _, mesh, _, _, _ in CANDIDATES if not cmds.objExists(mesh)]
    if missing:
        raise RuntimeError("Missing comparison meshes: %s" % missing)

    _reset_and_set_jaw25()

    label_group = _ensure_group("CDFDIAG_V086_COMPARE_LABELS_GRP")
    for key, mesh, _, label, note in CANDIDATES:
        _make_label("%s\n%s" % (label, note), mesh, label_group)

    all_meshes = [mesh for _, mesh, _, _, _ in CANDIDATES]
    all_set = _make_set("CDFDIAG_V086_COMPARE_ALL_MESHES_SET", all_meshes)
    rec_set = _make_set("CDFDIAG_V086_COMPARE_RECOMMENDED_SAFE005_SET", ["A_V086TEST_003_alphaSafe005"])

    scene_path = str(OUT_PATH)
    if OUT_PATH.exists():
        stem = OUT_PATH.with_suffix("")
        index = 2
        while True:
            candidate = Path(str(stem) + "_v%03d.ma" % index)
            if not candidate.exists():
                scene_path = str(candidate)
                break
            index += 1

    report = _write_reports(scene_path)
    cmds.select(rec_set, replace=True)
    cmds.file(rename=scene_path)
    cmds.file(save=True, type="mayaAscii")
    report["output_scene"] = scene_path
    report["all_set"] = all_set
    report["recommended_set"] = rec_set
    return report


result = main()
