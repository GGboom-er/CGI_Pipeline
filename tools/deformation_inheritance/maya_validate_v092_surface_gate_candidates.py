# -*- coding: utf-8 -*-
"""v092 actual-surface gated 候选的真实 Maya DG 表面验收。"""

from __future__ import annotations

from pathlib import Path
import traceback


ROOT = Path(r"Y:\GGbommer\scripts\CGI_Pipeline")
TEMPLATE = ROOT / "tools" / "deformation_inheritance" / "maya_validate_v090_patch_surface_objective.py"
PROJECT_DIR = ROOT / "projects" / "ysj" / "20260513_193837_cdfbaixingG"
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"


def _execute_from_template():
    source = TEMPLATE.read_text(encoding="utf-8")
    prefix = source.split("\ntry:\n    result = _execute()", 1)[0]
    prefix = prefix.replace("CDFDIAG_V090", "CDFDIAG_V092")
    ns = {"__name__": "v092_surface_gate_validator"}
    exec(compile(prefix, str(TEMPLATE), "exec"), ns)
    ns["SCENE"] = PROJECT_DIR / "test_v092_A_surfaceGate_candidates.ma"
    ns["OUTPUT_SCENE"] = PROJECT_DIR / "test_v092_A_surfaceGate_candidates_eval.ma"
    ns["REPORT_JSON"] = INFO_DIR / "v092_surface_gate_objective_report.json"
    ns["REPORT_CSV"] = INFO_DIR / "v092_surface_gate_objective_summary.csv"
    ns["ARRAYS_NPZ"] = INFO_DIR / "v092_surface_gate_objective_arrays.npz"
    ns["CANDIDATES"] = [
        (ns["BASE_KEY"], "A_V089B_001_baseHybrid"),
        ("v092_gate_tight", "A_V092_001_gateTight"),
        ("v092_gate_balanced", "A_V092_002_gateBalanced"),
        ("v092_gate_visible", "A_V092_003_gateVisible"),
        ("v092_gate_patch_loose", "A_V092_004_patchLoose"),
        ("v092_gate_patch_votes", "A_V092_005_patchVotes"),
    ]
    return ns["_execute"]()


try:
    result = _execute_from_template()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
