from __future__ import annotations

import json
import math
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.path_guard import is_protected_path
from core.receipt import make_receipt


@dataclass
class SourceGroup:
    label: str
    owner: str
    dags: list[str]
    vertices: np.ndarray
    vertex_count: int


def _lower_text(dag: str) -> str:
    return str(dag).replace("|", "_").replace("Shape", "").lower()


def _mesh_label(dag: str) -> str:
    leaf = str(dag).split("|")[-1]
    return leaf[:-5] if leaf.endswith("Shape") else leaf


def _classify_source(dag: str) -> tuple[str, str]:
    text = _lower_text(dag)
    if "body_msh" in text:
        return "body", "body"
    if "toufa" in text or "hair" in text:
        return "hair", "head_attachment"
    if "eyebrow" in text or "brow" in text:
        return "eyebrow", "head_attachment"
    if "yiniyng" in text or "eyeshadow" in text:
        return "eyeshadow", "head_attachment"
    if "upteeth" in text:
        return "upper_teeth", "mouth_upper"
    if "upgum" in text:
        return "upper_gum", "mouth_upper"
    if "loteeth" in text:
        return "lower_teeth", "mouth_lower"
    if "logum" in text:
        return "lower_gum", "mouth_lower"
    if "tongue" in text:
        return "tongue", "mouth_tongue"
    if "upeyelash" in text or "eyelash" in text:
        return "eyelash", "eyelid"
    if "eyeinside_l" in text:
        return "left_eye_inner", "eye"
    if "eyeoutside_l" in text:
        return "left_eye_outer", "eye"
    if "eyeinside_r" in text:
        return "right_eye_inner", "eye"
    if "eyeoutside_r" in text:
        return "right_eye_outer", "eye"
    if "highlight_l" in text:
        return "left_eyelight", "eye_highlight"
    if "highlight_r" in text:
        return "right_eyelight", "eye_highlight"
    if "coat" in text or "clothes" in text:
        return "coat", "cloth_outer"
    if "trouser" in text:
        return "trousers", "cloth_lower"
    if "shose" in text or "shoe" in text:
        return "shoe", "shoe"
    if "belt_001" in text:
        return "waistband2", "belt"
    if "belt_002" in text:
        return "waistband1", "belt"
    if "belt_003" in text:
        return "belt_accessory", "belt"
    if "headwear" in text:
        return "headwear", "head_attachment"
    if "fingernail" in text:
        return "fingernail", "hand_attachment"
    if "lacrimal" in text:
        return "lacrimal", "eye"
    return "unknown", "unknown"


def _target_intent(dag: str) -> dict:
    text = _lower_text(dag)
    if "teethup" in text:
        return {
            "owner": "mouth_upper",
            "expected_labels": ["upper_teeth", "upper_gum"],
            "forbidden_owners": ["eye", "hair", "cloth_outer", "body"],
        }
    if "teethlow" in text:
        return {
            "owner": "mouth_lower",
            "expected_labels": ["lower_teeth", "lower_gum"],
            "forbidden_owners": ["eye", "hair", "cloth_outer", "body"],
        }
    if "tongue" in text:
        return {
            "owner": "mouth_tongue",
            "expected_labels": ["tongue"],
            "forbidden_owners": ["eye", "hair", "cloth_outer", "body"],
        }
    if "eyebrow" in text:
        return {
            "owner": "head_attachment",
            "expected_labels": ["eyebrow"],
            "forbidden_owners": ["eye", "mouth_upper", "mouth_lower", "mouth_tongue"],
        }
    if "eyeshadow" in text:
        return {
            "owner": "head_attachment",
            "expected_labels": ["eyeshadow"],
            "forbidden_owners": ["mouth_upper", "mouth_lower", "mouth_tongue"],
        }
    if "body1" in text or "body_msh" in text:
        return {"owner": "body", "expected_labels": ["body"], "forbidden_owners": []}
    if "hair" in text:
        return {"owner": "head_attachment", "expected_labels": ["hair"], "forbidden_owners": ["mouth_upper", "mouth_lower"]}
    if "clothes" in text:
        return {"owner": "cloth_outer", "expected_labels": ["coat"], "forbidden_owners": []}
    if "trousers" in text:
        return {"owner": "cloth_lower", "expected_labels": ["trousers"], "forbidden_owners": []}
    if "shoe" in text:
        return {"owner": "shoe", "expected_labels": ["shoe"], "forbidden_owners": []}
    if "waistband1" in text:
        return {"owner": "belt", "expected_labels": ["waistband1"], "forbidden_owners": []}
    if "waistband2" in text:
        return {"owner": "belt", "expected_labels": ["waistband2"], "forbidden_owners": []}
    if "accessories3" in text:
        return {"owner": "belt", "expected_labels": ["belt_accessory"], "forbidden_owners": []}
    if "accessories" in text:
        return {"owner": "head_attachment", "expected_labels": ["headwear"], "forbidden_owners": []}
    if "eyeball" in text and "_l_" in text:
        return {"owner": "eye", "expected_labels": ["left_eye_inner"], "forbidden_owners": ["mouth_upper", "mouth_lower"]}
    if "vitreous" in text and "_l_" in text:
        return {"owner": "eye", "expected_labels": ["left_eye_outer"], "forbidden_owners": ["mouth_upper", "mouth_lower"]}
    if "eyeball" in text and "_r_" in text:
        return {"owner": "eye", "expected_labels": ["right_eye_inner"], "forbidden_owners": ["mouth_upper", "mouth_lower"]}
    if "vitreous" in text and "_r_" in text:
        return {"owner": "eye", "expected_labels": ["right_eye_outer"], "forbidden_owners": ["mouth_upper", "mouth_lower"]}
    if "eyelight" in text and "_l_" in text:
        return {"owner": "eye_highlight", "expected_labels": ["left_eyelight"], "forbidden_owners": ["mouth_upper", "mouth_lower"]}
    if "eyelight" in text and "_r_" in text:
        return {"owner": "eye_highlight", "expected_labels": ["right_eyelight"], "forbidden_owners": ["mouth_upper", "mouth_lower"]}
    return {"owner": "unknown", "expected_labels": [], "forbidden_owners": []}


def _sample_vertices(vertices: np.ndarray, sample_max: int = 1800) -> np.ndarray:
    if vertices.shape[0] <= sample_max:
        return vertices
    idx = np.linspace(0, vertices.shape[0] - 1, sample_max, dtype=np.int64)
    return vertices[idx]


def _bbox_iou(a: np.ndarray, b: np.ndarray) -> float:
    amin, amax = a.min(axis=0), a.max(axis=0)
    bmin, bmax = b.min(axis=0), b.max(axis=0)
    inter_min = np.maximum(amin, bmin)
    inter_max = np.minimum(amax, bmax)
    inter_size = np.maximum(inter_max - inter_min, 0.0)
    inter_vol = float(np.prod(inter_size))
    av = float(np.prod(np.maximum(amax - amin, 0.0)))
    bv = float(np.prod(np.maximum(bmax - bmin, 0.0)))
    union = av + bv - inter_vol
    return inter_vol / union if union > 1e-12 else 0.0


def _chamfer(a: np.ndarray, b: np.ndarray) -> float:
    from scipy.spatial import cKDTree

    aa = _sample_vertices(a)
    bb = _sample_vertices(b)
    tb = cKDTree(bb)
    ta = cKDTree(aa)
    da, _ = tb.query(aa, k=1)
    db, _ = ta.query(bb, k=1)
    return float((np.mean(da) + np.mean(db)) * 0.5)


def _score_group(target_vertices: np.ndarray, target_count: int, intent: dict, group: SourceGroup) -> dict:
    chamfer = _chamfer(target_vertices, group.vertices)
    diag = float(np.linalg.norm(target_vertices.max(axis=0) - target_vertices.min(axis=0)))
    diag = max(diag, 1e-6)
    bbox_iou = _bbox_iou(target_vertices, group.vertices)
    expected = set(intent.get("expected_labels") or [])
    forbidden = group.owner in set(intent.get("forbidden_owners") or [])

    semantic_score = 0.0
    composite_bonus = 0.0
    if group.label in expected:
        semantic_score = 1.0
    elif group.label.endswith("_composite"):
        if group.owner == intent.get("owner"):
            semantic_score = 1.08
            if abs(group.vertex_count - target_count) <= max(2, int(target_count * 0.02)):
                composite_bonus = 0.12
    elif group.owner == intent.get("owner") and intent.get("owner") != "unknown":
        semantic_score = 0.82

    geo_score = math.exp(-chamfer / (diag * 0.08))
    bbox_score = min(1.0, bbox_iou * 4.0)
    count_ratio = min(group.vertex_count, target_count) / max(group.vertex_count, target_count, 1)

    score = (
        0.42 * semantic_score
        + 0.24 * geo_score
        + 0.16 * bbox_score
        + 0.18 * count_ratio
        + composite_bonus
    )
    if forbidden:
        score *= 0.15
    return {
        "label": group.label,
        "owner": group.owner,
        "dags": list(group.dags),
        "source_vertex_count": group.vertex_count,
        "chamfer": round(chamfer, 6),
        "bbox_iou": round(bbox_iou, 6),
        "semantic_score": round(semantic_score, 6),
        "geo_score": round(geo_score, 6),
        "bbox_score": round(bbox_score, 6),
        "count_score": round(count_ratio, 6),
        "composite_bonus": round(composite_bonus, 6),
        "forbidden": forbidden,
        "score": round(min(score, 1.0), 6),
    }


def _build_source_groups(source_meshes: list[dict]) -> list[SourceGroup]:
    groups: list[SourceGroup] = []
    by_label: dict[str, list[dict]] = {}
    for mesh in source_meshes:
        label, owner = _classify_source(mesh["dag"])
        by_label.setdefault(label, []).append(mesh)
        groups.append(
            SourceGroup(
                label=label,
                owner=owner,
                dags=[mesh["dag"]],
                vertices=np.asarray(mesh["vertices"], dtype=np.float64),
                vertex_count=int(mesh["vertices"].shape[0]),
            )
        )

    for label, owner, parts in [
        ("mouth_upper_composite", "mouth_upper", ["upper_teeth", "upper_gum"]),
        ("mouth_lower_composite", "mouth_lower", ["lower_teeth", "lower_gum"]),
    ]:
        meshes = []
        for part in parts:
            meshes.extend(by_label.get(part, []))
        if len(meshes) < 2:
            continue
        groups.append(
            SourceGroup(
                label=label,
                owner=owner,
                dags=[m["dag"] for m in meshes],
                vertices=np.vstack([m["vertices"] for m in meshes]),
                vertex_count=int(sum(m["vertices"].shape[0] for m in meshes)),
            )
        )
    return groups


def _best_owner(target_mesh: dict, groups: list[SourceGroup]) -> dict:
    intent = _target_intent(target_mesh["dag"])
    target_vertices = np.asarray(target_mesh["vertices"], dtype=np.float64)
    candidates = [
        _score_group(target_vertices, int(target_vertices.shape[0]), intent, group)
        for group in groups
    ]
    candidates.sort(key=lambda row: row["score"], reverse=True)
    best = candidates[0] if candidates else {}
    margin = 0.0
    if len(candidates) > 1:
        margin = round(best["score"] - candidates[1]["score"], 6)
    verdict = "ACCEPT"
    if not best or best.get("score", 0.0) < 0.55 or margin < 0.03:
        verdict = "LOW_CONF"
    return {
        "intent": intent,
        "best_candidate": best,
        "margin": margin,
        "verdict": verdict,
        "candidates": candidates[:8],
    }


def _normalize_weights(weights: np.ndarray) -> np.ndarray:
    weights = np.maximum(np.asarray(weights, dtype=np.float64), 0.0)
    row_sums = weights.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    return weights / row_sums


def _prune_weights(weights: np.ndarray, max_influences: int) -> np.ndarray:
    if max_influences <= 0 or max_influences >= weights.shape[1]:
        return _normalize_weights(weights)
    out = np.zeros_like(weights)
    idx = np.argpartition(-weights, kth=max_influences - 1, axis=1)[:, :max_influences]
    rows = np.arange(weights.shape[0])[:, None]
    out[rows, idx] = weights[rows, idx]
    return _normalize_weights(out)


def _top_joints(weights: np.ndarray, joints: list[str], limit: int = 5) -> list[dict]:
    top = np.argmax(weights, axis=1)
    unique, counts = np.unique(top, return_counts=True)
    order = np.argsort(-counts)
    return [
        {
            "joint": joints[int(unique[i])],
            "count": int(counts[i]),
            "ratio": round(float(counts[i] / len(top)), 6),
        }
        for i in order[:limit]
    ]


def _weight_metrics(pred: np.ndarray, gt: np.ndarray | None) -> dict | None:
    if gt is None:
        return None
    diff = np.abs(pred - gt)
    l1 = diff.sum(axis=1)
    top_pred = np.argmax(pred, axis=1)
    top_gt = np.argmax(gt, axis=1)
    return {
        "l1_mean": float(np.mean(l1)),
        "l1_p95": float(np.percentile(l1, 95)),
        "l1_max": float(np.max(l1)),
        "top1_match": float(np.mean(top_pred == top_gt)),
    }


def _resolve_info_dir(payload: dict, params: dict, source_path: str) -> Path:
    info_dir = params.get("info_dir") or payload.get("info_dir") or (payload.get("extra_params") or {}).get("info_dir")
    if info_dir:
        return Path(info_dir).resolve()
    scene_dir = Path(source_path).resolve().parent if source_path else Path.cwd()
    return scene_dir / ".info"


def _ensure_writable_path(path: Path) -> None:
    if is_protected_path(str(path)):
        raise RuntimeError(f"输出路径位于受保护区域，禁止写入: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)


def _backup_existing_file(path: Path) -> str:
    if not path.exists():
        return ""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.bak_{stamp}")
    shutil.copy2(path, backup)
    return str(backup)


def _open_scene_if_needed(source_path: str) -> None:
    import maya.cmds as cmds

    current = cmds.file(query=True, sceneName=True) or ""
    if source_path and Path(source_path).resolve() != Path(current).resolve():
        cmds.file(source_path, open=True, force=True, ignoreVersion=True, prompt=False)


def _visible_mesh_shapes(root: str) -> list[str]:
    import maya.cmds as cmds

    if not cmds.objExists(root):
        raise RuntimeError(f"未找到 mesh 根节点: {root}")
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    return sorted(
        shape for shape in shapes
        if cmds.objExists(shape) and not cmds.getAttr(shape + ".intermediateObject")
    )


def _extract_group_data(root: str, joint_order: list[str] | None = None) -> dict:
    from core.maya_data_bridge import extract_mesh_geometry, extract_skin_weights

    shapes = _visible_mesh_shapes(root)
    meshes = []
    joint_set = set(joint_order or [])
    errors = []
    for shape in shapes:
        try:
            geom = extract_mesh_geometry(shape)
            skin = extract_skin_weights(shape)
            if skin:
                joint_set.update(skin["joints"])
            meshes.append(
                {
                    "dag": shape,
                    "mesh": _mesh_label(shape),
                    "vertices": geom["vertices"],
                    "faces": geom["faces"],
                    "normals": geom["normals"],
                    "skin_joints": skin["joints"] if skin else [],
                    "skin_weights_local": skin["weights"] if skin else None,
                    "weights": None,
                    "has_skin": bool(skin),
                }
            )
        except Exception as exc:
            errors.append({"dag": shape, "error": repr(exc)})

    all_joints = list(joint_order or sorted(joint_set))
    for mesh in meshes:
        local = mesh.pop("skin_weights_local")
        local_joints = mesh.get("skin_joints") or []
        if local is None:
            continue
        global_w = np.zeros((local.shape[0], len(all_joints)), dtype=np.float64)
        joint_index = {name: i for i, name in enumerate(all_joints)}
        for li, joint in enumerate(local_joints):
            gi = joint_index.get(joint)
            if gi is not None:
                global_w[:, gi] = local[:, li]
        mesh["weights"] = _normalize_weights(global_w)
    return {"all_joints": all_joints, "meshes": meshes, "errors": errors}


def _field_for_sources(joints: list[str], source_meshes: list[dict]):
    from core.deformation_field import DeformationField

    rig_data = {
        "all_joints": joints,
        "meshes": [
            {
                "name": mesh["mesh"],
                "vertices": np.ascontiguousarray(mesh["vertices"], dtype=np.float64),
                "faces": np.ascontiguousarray(mesh["faces"], dtype=np.int64),
                "normals": np.ascontiguousarray(mesh["normals"], dtype=np.float64),
                "weights": np.ascontiguousarray(mesh["weights"], dtype=np.float64),
            }
            for mesh in source_meshes
            if mesh.get("weights") is not None
        ],
    }
    if not rig_data["meshes"]:
        return None
    return DeformationField(rig_data)


def _predict_owner_filtered(source_data: dict, target_data: dict, max_influences: int) -> tuple[dict, dict]:
    joints = source_data["all_joints"]
    source_meshes = [mesh for mesh in source_data["meshes"] if mesh.get("weights") is not None]
    source_by_dag = {mesh["dag"]: mesh for mesh in source_meshes}
    groups = _build_source_groups(source_meshes)

    arrays = {
        "meta_joints": np.array(json.dumps(joints, ensure_ascii=False)),
        "meta_mesh_keys": np.array(json.dumps([mesh["dag"] for mesh in target_data["meshes"]], ensure_ascii=False)),
        "meta_algorithm": np.array("owner_filtered"),
    }
    rows = []
    low_conf = 0

    for idx, target in enumerate(target_data["meshes"]):
        owner = _best_owner(target, groups)
        best = owner.get("best_candidate") or {}
        source_dags = best.get("dags") or []
        selected_sources = [source_by_dag[dag] for dag in source_dags if dag in source_by_dag]
        status = "SUCCESS"
        if not selected_sources:
            status = "NO_OWNER_SOURCE"
            selected_sources = source_meshes
        if owner["verdict"] != "ACCEPT":
            low_conf += 1

        field = _field_for_sources(joints, selected_sources)
        if field is None:
            raise RuntimeError(f"无法为 {target['mesh']} 构建 source field")
        weights, stats = field.sample_weights(
            target["vertices"],
            target["faces"],
            new_normals=target.get("normals"),
            use_winding=False,
            max_normal_angle_deg=90.0,
            smooth_iterations=0,
            idw_k=8,
            idw_blend=0.3,
        )
        weights = _prune_weights(weights, max_influences)
        arrays[f"{idx}_weights"] = weights
        rows.append(
            {
                "dag": target["dag"],
                "mesh": target["mesh"],
                "vertices": int(target["vertices"].shape[0]),
                "has_ground_truth_skin": bool(target.get("has_skin")),
                "status": status,
                "owner_verdict": owner["verdict"],
                "owner": best.get("owner"),
                "owner_label": best.get("label"),
                "owner_score": best.get("score"),
                "owner_margin": owner.get("margin"),
                "source_dags": source_dags,
                "top_joints": _top_joints(weights, joints),
                "metrics_vs_ground_truth": _weight_metrics(weights, target.get("weights")),
                "stats": stats,
                "candidates": owner.get("candidates", [])[:5],
            }
        )

    diagnostic = {
        "algorithm": "owner_filtered",
        "joint_count": len(joints),
        "mesh_count": len(rows),
        "predicted_count": len(rows),
        "low_confidence_count": low_conf,
        "meshes": rows,
    }
    return arrays, diagnostic


def _parent_transform(shape: str) -> str:
    import maya.cmds as cmds

    parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
    if not parents:
        raise RuntimeError(f"未找到 shape 的 transform: {shape}")
    return parents[0]


def _skin_cluster(shape: str) -> str | None:
    import maya.cmds as cmds

    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    skins = cmds.ls(history, type="skinCluster") or []
    return skins[0] if skins else None


def _delete_existing_skin(shape: str) -> list[str]:
    import maya.cmds as cmds

    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    skins = cmds.ls(history, type="skinCluster") or []
    if skins:
        cmds.delete(skins)
    return skins


def _apply_skin_weights(source_path: str, weights_npz: Path, output_path: Path, apply_scope: str) -> dict:
    import maya.cmds as cmds
    from core.maya_data_bridge import inject_skin_weights

    _open_scene_if_needed(source_path)
    pred = np.load(weights_npz, allow_pickle=True)
    joints = json.loads(str(pred["meta_joints"]))
    mesh_keys = json.loads(str(pred["meta_mesh_keys"]))

    rows = []
    applied = 0
    skipped = 0
    cmds.undoInfo(openChunk=True, chunkName="maya_deformation_inherit_skin")
    try:
        for idx, shape in enumerate(mesh_keys):
            if not cmds.objExists(shape):
                rows.append({"dag": shape, "status": "MISSING_IN_SCENE"})
                continue
            existing_skin = _skin_cluster(shape)
            if apply_scope == "missing_only" and existing_skin:
                skipped += 1
                rows.append({"dag": shape, "status": "SKIPPED_HAS_SKIN", "skinCluster": existing_skin})
                continue
            deleted = _delete_existing_skin(shape)
            transform = _parent_transform(shape)
            weights = pred[f"{idx}_weights"]
            inject_skin_weights(transform, joints, weights)
            applied += 1
            rows.append(
                {
                    "dag": shape,
                    "transform": transform,
                    "status": "SUCCESS",
                    "deleted_skin_clusters": deleted,
                    "vertices": int(weights.shape[0]),
                }
            )
    finally:
        cmds.undoInfo(closeChunk=True)

    _ensure_writable_path(output_path)
    backup_path = _backup_existing_file(output_path)
    cmds.file(rename=str(output_path))
    cmds.file(save=True, type="mayaAscii")
    return {
        "applied_count": applied,
        "skipped_count": skipped,
        "write_rows": rows,
        "backup_path": backup_path,
    }


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get("parameters", {}) or {}
    source_path = payload.get("source_path") or params.get("source_path") or ""
    reference_rig_path = params.get("reference_rig_path") or ""
    reference_group = params.get("reference_group") or ""
    target_group = params.get("target_group") or "|Group|Geometry|cache"
    mode = params.get("mode") or "diagnose_only"
    apply_scope = params.get("apply_scope") or "missing_only"
    max_influences = int(params.get("max_influences") or 0)
    output_path_text = params.get("output_path") or ""

    input_record = {
        "source_path": source_path,
        "reference_rig_path": reference_rig_path,
        "reference_group": reference_group,
        "target_group": target_group,
        "mode": mode,
        "apply_scope": apply_scope,
        "max_influences": max_influences,
    }

    try:
        if mode not in {"diagnose_only", "apply_skin"}:
            raise RuntimeError(f"mode 只支持 diagnose_only/apply_skin: {mode}")
        if apply_scope not in {"missing_only", "all"}:
            raise RuntimeError(f"apply_scope 只支持 missing_only/all: {apply_scope}")
        if not source_path or not Path(source_path).exists():
            raise RuntimeError(f"目标场景 source_path 不存在: {source_path}")
        if not reference_rig_path or not Path(reference_rig_path).exists():
            raise RuntimeError(f"旧 Rig reference_rig_path 不存在: {reference_rig_path}")
        if not reference_group:
            raise RuntimeError("缺少必填参数 reference_group")
        if mode == "apply_skin" and not output_path_text:
            raise RuntimeError("apply_skin 模式必须提供 output_path")

        info_dir = _resolve_info_dir(payload, params, source_path)
        _ensure_writable_path(info_dir / "placeholder.tmp")
        placeholder = info_dir / "placeholder.tmp"
        if placeholder.exists():
            placeholder.unlink()
        weights_path = info_dir / "deformation_inherit_skin_weights.npz"
        diagnostic_path = info_dir / "deformation_inherit_skin_diagnostic.json"
        _ensure_writable_path(weights_path)
        _ensure_writable_path(diagnostic_path)
        artifact_backups = [
            path for path in (
                _backup_existing_file(weights_path),
                _backup_existing_file(diagnostic_path),
            )
            if path
        ]

        import maya.cmds as cmds

        _open_scene_if_needed(source_path)
        target_data = _extract_group_data(target_group)
        cmds.file(reference_rig_path, open=True, force=True, ignoreVersion=True, prompt=False)
        source_data = _extract_group_data(reference_group)

        # target 权重需要按 source joint order 对齐。
        _open_scene_if_needed(source_path)
        target_data = _extract_group_data(target_group, joint_order=source_data["all_joints"])

        arrays, diagnostic = _predict_owner_filtered(source_data, target_data, max_influences=max_influences)
        np.savez_compressed(weights_path, **arrays)

        apply_result = {
            "applied_count": 0,
            "skipped_count": 0,
            "write_rows": [],
            "backup_path": "",
        }
        main_output_path = diagnostic_path
        if mode == "apply_skin":
            output_scene = Path(output_path_text).resolve()
            apply_result = _apply_skin_weights(source_path, weights_path, output_scene, apply_scope)
            main_output_path = output_scene

        diagnostic["input"] = input_record
        diagnostic["source_errors"] = source_data.get("errors", [])
        diagnostic["target_errors"] = target_data.get("errors", [])
        diagnostic["apply"] = apply_result
        diagnostic["artifact_backups"] = artifact_backups
        diagnostic["elapsed_sec"] = round(time.time() - t0, 3)
        diagnostic_path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")

        output = {
            "output_path": str(main_output_path),
            "weights_path": str(weights_path),
            "diagnostic_path": str(diagnostic_path),
            "mesh_count": diagnostic["mesh_count"],
            "predicted_count": diagnostic["predicted_count"],
            "applied_count": apply_result["applied_count"],
            "skipped_count": apply_result["skipped_count"],
            "low_confidence_count": diagnostic["low_confidence_count"],
        }
        if artifact_backups:
            output["artifact_backups"] = artifact_backups
        if apply_result.get("backup_path"):
            output["backup_path"] = apply_result["backup_path"]

        return make_receipt(
            skill_id="maya_deformation_inherit_skin",
            status="SUCCESS",
            start_time=t0,
            input=input_record,
            output=output,
        )
    except Exception as exc:
        return make_receipt(
            skill_id="maya_deformation_inherit_skin",
            status="ERROR",
            start_time=t0,
            input=input_record,
            output={},
            error=repr(exc),
        )
