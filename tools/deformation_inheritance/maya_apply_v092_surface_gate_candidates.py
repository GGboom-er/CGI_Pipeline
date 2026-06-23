# -*- coding: utf-8 -*-
"""把 v092 actual-surface gated 候选写成 Maya 对照 mesh。"""

from __future__ import annotations

from pathlib import Path
import traceback


ROOT = Path(r"Y:\GGbommer\scripts\CGI_Pipeline")
TEMPLATE = ROOT / "tools" / "deformation_inheritance" / "maya_apply_v091_patch_candidates.py"
PROJECT_DIR = ROOT / "projects" / "ysj" / "20260513_193837_cdfbaixingG"
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"


def _execute_from_template():
    source = TEMPLATE.read_text(encoding="utf-8")
    prefix = source.split("\ntry:\n    result = _execute()", 1)[0]
    prefix = prefix.replace("CDFDIAG_V091", "CDFDIAG_V092")
    ns = {"__name__": "v092_surface_gate_apply"}
    exec(compile(prefix, str(TEMPLATE), "exec"), ns)
    ns["INPUT_SCENE"] = PROJECT_DIR / "test_v090_A_patchSurface_candidates_eval.ma"
    ns["CANDIDATE_NPZ"] = INFO_DIR / "v092_surface_gate_candidates.npz"
    ns["REPORT_JSON"] = INFO_DIR / "v092_maya_apply_report.json"
    ns["OUTPUT_SCENE"] = PROJECT_DIR / "test_v092_A_surfaceGate_candidates.ma"
    ns["VARIANTS"] = [
        ("A_V092_001_gateTight", "weights__v092_gate_tight", 1740.0, (0.20, 0.78, 1.00)),
        ("A_V092_002_gateBalanced", "weights__v092_gate_balanced", 1860.0, (0.98, 0.45, 0.18)),
        ("A_V092_003_gateVisible", "weights__v092_gate_visible", 1980.0, (0.55, 0.92, 0.35)),
        ("A_V092_004_patchLoose", "weights__v092_gate_patch_loose", 2100.0, (0.75, 0.45, 1.00)),
        ("A_V092_005_patchVotes", "weights__v092_gate_patch_votes", 2220.0, (1.00, 0.80, 0.20)),
    ]
    return ns["_execute"]()


try:
    result = _execute_from_template()
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
