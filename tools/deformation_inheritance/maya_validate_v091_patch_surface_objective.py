# -*- coding: utf-8 -*-
"""v091 patch constrained objective 的真实 Maya DG 表面验收。

复用 v090 的已验证统计函数，但替换为 v091 场景、候选和报告路径。
"""

from __future__ import annotations

from pathlib import Path
import traceback


ROOT = Path(r"Y:\GGbommer\scripts\CGI_Pipeline")
TEMPLATE = ROOT / "tools" / "deformation_inheritance" / "maya_validate_v090_patch_surface_objective.py"
PROJECT_DIR = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG")
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"


def _execute_from_template():
    source = TEMPLATE.read_text(encoding="utf-8")
    prefix = source.split("\ntry:\n    result = _execute()", 1)[0]
    prefix = prefix.replace("CDFDIAG_V090", "CDFDIAG_V091")
    ns = {"__name__": "v091_patch_surface_validator"}
    exec(compile(prefix, str(TEMPLATE), "exec"), ns)
    ns["SCENE"] = PROJECT_DIR / "test_v091_A_patchConstrained_candidates.ma"
    ns["OUTPUT_SCENE"] = PROJECT_DIR / "test_v091_A_patchConstrained_candidates_eval.ma"
    ns["REPORT_JSON"] = INFO_DIR / "v091_patch_surface_candidates_objective_report.json"
    ns["REPORT_CSV"] = INFO_DIR / "v091_patch_surface_candidates_objective_summary.csv"
    ns["ARRAYS_NPZ"] = INFO_DIR / "v091_patch_surface_candidates_objective_arrays.npz"
    ns["CANDIDATES"] = [
        (ns["BASE_KEY"], "A_V089B_001_baseHybrid"),
        ("v091_surface_balanced", "A_V091_001_surfaceBalanced"),
        ("v091_surface_visible", "A_V091_002_surfaceVisible"),
        ("v091_surface_shape_guard", "A_V091_003_shapeGuard"),
    ]
    return ns["_execute"]()


try:
    result = _execute_from_template()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
