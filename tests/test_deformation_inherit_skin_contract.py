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

    faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=int)
    components = mod._connected_components(6, faces)
    _check("connected components split disconnected shells", len(components) == 2)
    _check("component vertex count", sorted(len(c["vertex_indices"]) for c in components) == [3, 3])

    group = mod.SourceGroup(
        label="body",
        owner="body",
        dags=["|old|body_msh|body_mshShape"],
        vertices=np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [10.0, 10.0, 0.0],
            ],
            dtype=float,
        ),
        vertex_count=4,
    )
    subset = {"dag": "|new|unknownShape#component_0", "vertices": group.vertices[:3]}
    owner = mod._best_owner(subset, [group], partial=True)
    _check("partial owner accepts source superset", owner["best_candidate"]["label"] == "body")

    belt_group = mod.SourceGroup(
        label="waistband1",
        owner="belt",
        dags=["|old|belt_002_msh|belt_002_mshShape"],
        vertices=np.array(
            [
                [10.0, 0.0, 0.0],
                [11.0, 0.0, 0.0],
                [10.0, 1.0, 0.0],
                [11.0, 1.0, 0.0],
            ],
            dtype=float,
        ),
        vertex_count=4,
    )
    patch_target = {
        "dag": "|new|cdfBaiXingG_welded_all1|cdfBaiXingG_welded_all1Shape#component_0",
        "mesh": "cdfBaiXingG_welded_all1#component_0",
        "vertices": np.array(
            [
                [0.1, 0.0, 0.0],
                [0.8, 0.0, 0.0],
                [0.1, 0.9, 0.0],
                [10.1, 0.0, 0.0],
                [10.9, 0.0, 0.0],
                [10.1, 0.9, 0.0],
            ],
            dtype=float,
        ),
    }
    assignments = mod._patch_owner_assignments(patch_target, [group, belt_group])
    labels = sorted(row["label"] for row in assignments)
    _check("patch owner splits mixed component", labels == ["body", "waistband1"])
    _check("patch owner keeps all vertices", sum(row["vertices"] for row in assignments) == 6)

    print("[PASS] deformation inherit skin contract")


if __name__ == "__main__":
    main()
