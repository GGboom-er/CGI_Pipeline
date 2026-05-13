import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.maya_deformation_inherit_skin import maya_deformation_inherit_skin as mod
import numpy as np


def _check(name, condition):
    if condition:
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name}")
        raise SystemExit(1)


def main():
    _check("source body classify", mod._classify_source("|A|geo|body|body_msh|body_mshShape") == ("body", "body"))
    _check("source upper teeth classify", mod._classify_source("|A|upteeth_msh|upteeth_mshShape") == ("upper_teeth", "mouth_upper"))
    _check("target teeth intent", mod._target_intent("|cache|cdfBaiXingG_teethup1|cdfBaiXingG_teethup1Shape")["owner"] == "mouth_upper")

    weights = np.array([[0.2, 0.3, 0.5], [0.0, 0.0, 0.0]], dtype=float)
    normalized = mod._normalize_weights(weights)
    _check("normalize first row", abs(float(normalized[0].sum()) - 1.0) < 1e-9)
    _check("normalize zero row stable", float(normalized[1].sum()) == 0.0)

    pruned = mod._prune_weights(np.array([[0.1, 0.2, 0.7]], dtype=float), 2)
    _check("prune keeps two influences", int((pruned[0] > 0).sum()) == 2)
    _check("prune normalized", abs(float(pruned[0].sum()) - 1.0) < 1e-9)

    print("[PASS] deformation inherit skin contract")


if __name__ == "__main__":
    main()
