from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree


PROJECT_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG"
)
INFO_DIR = PROJECT_DIR / ".info" / "a_weight_transfer_v082_reboot"
DEFAULT_INPUT = INFO_DIR / "v082_clean_scene_data.npz"


FAMILIES = [
    "upper_mouth",
    "lower_mouth",
    "mouth_corner",
    "upper_lid",
    "lower_lid",
    "eye",
    "cheek",
    "nose",
    "brow",
    "ear",
    "head",
    "neck",
    "other",
]
FAMILY_INDEX = {name: i for i, name in enumerate(FAMILIES)}


def _leaf(path: str) -> str:
    return str(path).split("|")[-1].split(":")[-1].lower()


def classify_influence(path: str) -> str:
    name = _leaf(path)
    compact = name.replace("_", "")
    if "mouthc" in compact or "mouthcorner" in compact or "lipcorner" in compact:
        return "mouth_corner"
    if "uplip" in compact or "upperlip" in compact or "jawup" in compact:
        return "upper_mouth"
    if (
        "lolip" in compact
        or "lowerlip" in compact
        or "lowlip" in compact
        or "chin" in compact
        or ("jaw" in compact and "jawup" not in compact)
    ):
        return "lower_mouth"
    if "uplid" in compact or "upperlid" in compact:
        return "upper_lid"
    if "lolid" in compact or "lowerlid" in compact:
        return "lower_lid"
    if "eyeball" in compact or name.startswith(("l_eye", "r_eye")):
        return "eye"
    if "cheek" in compact:
        return "cheek"
    if "nose" in compact:
        return "nose"
    if "brow" in compact:
        return "brow"
    if "ear" in compact:
        return "ear"
    if "neck" in compact:
        return "neck"
    if "head" in compact:
        return "head"
    return "other"


def faces_from_flat(counts: np.ndarray, offsets: np.ndarray, flat: np.ndarray) -> list[np.ndarray]:
    faces: list[np.ndarray] = []
    for i, count in enumerate(counts.astype(int)):
        if count < 3:
            continue
        start = int(offsets[i])
        faces.append(flat[start : start + count].astype(np.int32))
    return faces


def triangulate(faces: list[np.ndarray]) -> np.ndarray:
    tris: list[tuple[int, int, int]] = []
    for face in faces:
        if len(face) == 3:
            tris.append((int(face[0]), int(face[1]), int(face[2])))
        else:
            root = int(face[0])
            for i in range(1, len(face) - 1):
                tris.append((root, int(face[i]), int(face[i + 1])))
    return np.asarray(tris, dtype=np.int32)


def build_edges(faces: list[np.ndarray]) -> np.ndarray:
    edges: set[tuple[int, int]] = set()
    for face in faces:
        n = len(face)
        for i in range(n):
            a = int(face[i])
            b = int(face[(i + 1) % n])
            if a == b:
                continue
            if a > b:
                a, b = b, a
            edges.add((a, b))
    return np.asarray(sorted(edges), dtype=np.int32)


def safe_normalize(v: np.ndarray, axis: int = -1) -> np.ndarray:
    length = np.linalg.norm(v, axis=axis, keepdims=True)
    length[length == 0.0] = 1.0
    return v / length


def triangle_normals(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    a = points[tris[:, 0]]
    b = points[tris[:, 1]]
    c = points[tris[:, 2]]
    return safe_normalize(np.cross(b - a, c - a))


def vertex_normals(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    normals = np.zeros_like(points, dtype=np.float64)
    tri_normals = triangle_normals(points, tris)
    tri_area_vec = np.cross(points[tris[:, 1]] - points[tris[:, 0]], points[tris[:, 2]] - points[tris[:, 0]])
    tri_weight = np.maximum(np.linalg.norm(tri_area_vec, axis=1), 1e-12)
    weighted = tri_normals * tri_weight[:, None]
    for corner in range(3):
        np.add.at(normals, tris[:, corner], weighted)
    return safe_normalize(normals)


def edge_median(points: np.ndarray, edges: np.ndarray) -> float:
    if len(edges) == 0:
        return 1.0
    lengths = np.linalg.norm(points[edges[:, 0]] - points[edges[:, 1]], axis=1)
    return float(np.median(lengths))


def closest_point_tri_batch(point: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray):
    ab = b - a
    ac = c - a
    ap = point[None, :] - a
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    k = len(a)
    closest = np.empty((k, 3), dtype=np.float64)
    bary = np.zeros((k, 3), dtype=np.float64)
    remaining = np.ones(k, dtype=bool)

    mask = remaining & (d1 <= 0.0) & (d2 <= 0.0)
    closest[mask] = a[mask]
    bary[mask, 0] = 1.0
    remaining[mask] = False

    bp = point[None, :] - b
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    mask = remaining & (d3 >= 0.0) & (d4 <= d3)
    closest[mask] = b[mask]
    bary[mask, 1] = 1.0
    remaining[mask] = False

    vc = d1 * d4 - d3 * d2
    mask = remaining & (vc <= 0.0) & (d1 >= 0.0) & (d3 <= 0.0)
    denom = d1 - d3
    v = np.divide(d1, denom, out=np.zeros_like(d1), where=np.abs(denom) > 1e-12)
    closest[mask] = a[mask] + v[mask, None] * ab[mask]
    bary[mask, 0] = 1.0 - v[mask]
    bary[mask, 1] = v[mask]
    remaining[mask] = False

    cp = point[None, :] - c
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)
    mask = remaining & (d6 >= 0.0) & (d5 <= d6)
    closest[mask] = c[mask]
    bary[mask, 2] = 1.0
    remaining[mask] = False

    vb = d5 * d2 - d1 * d6
    mask = remaining & (vb <= 0.0) & (d2 >= 0.0) & (d6 <= 0.0)
    denom = d2 - d6
    w = np.divide(d2, denom, out=np.zeros_like(d2), where=np.abs(denom) > 1e-12)
    closest[mask] = a[mask] + w[mask, None] * ac[mask]
    bary[mask, 0] = 1.0 - w[mask]
    bary[mask, 2] = w[mask]
    remaining[mask] = False

    va = d3 * d6 - d5 * d4
    bc = c - b
    denom = (d4 - d3) + (d5 - d6)
    w2 = np.divide(d4 - d3, denom, out=np.zeros_like(d4), where=np.abs(denom) > 1e-12)
    mask = remaining & (va <= 0.0) & ((d4 - d3) >= 0.0) & ((d5 - d6) >= 0.0)
    closest[mask] = b[mask] + w2[mask, None] * bc[mask]
    bary[mask, 1] = 1.0 - w2[mask]
    bary[mask, 2] = w2[mask]
    remaining[mask] = False

    denom = va + vb + vc
    inv = np.divide(1.0, denom, out=np.zeros_like(denom), where=np.abs(denom) > 1e-12)
    v3 = vb * inv
    w3 = vc * inv
    u3 = 1.0 - v3 - w3
    mask = remaining
    closest[mask] = (
        u3[mask, None] * a[mask]
        + v3[mask, None] * b[mask]
        + w3[mask, None] * c[mask]
    )
    bary[mask, 0] = u3[mask]
    bary[mask, 1] = v3[mask]
    bary[mask, 2] = w3[mask]

    dist2 = np.einsum("ij,ij->i", closest - point[None, :], closest - point[None, :])
    bary = np.clip(bary, 0.0, 1.0)
    row_sum = bary.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0.0] = 1.0
    bary /= row_sum
    return closest, bary, dist2


def family_matrix(influence_names: np.ndarray) -> tuple[np.ndarray, list[str]]:
    labels = [classify_influence(str(name)) for name in influence_names]
    matrix = np.zeros((len(labels), len(FAMILIES)), dtype=np.float64)
    for i, label in enumerate(labels):
        matrix[i, FAMILY_INDEX[label]] = 1.0
    return matrix, labels


def summarize_indices(indices: np.ndarray, limit: int = 120) -> list[int]:
    if len(indices) == 0:
        return []
    return [int(x) for x in indices[:limit]]


def reason_string(i: int, flags: dict[str, np.ndarray]) -> str:
    reasons = []
    for name, values in flags.items():
        if bool(values[i]):
            reasons.append(name)
    return "|".join(reasons) if reasons else "ok"


def run_validator(
    input_path: Path,
    output_dir: Path,
    candidate_k: int = 48,
    output_prefix: str = "v082",
    selection_mode: str = "distance",
    normal_weight: float = 0.0,
    family_purity_weight: float = 0.0,
    normal_mismatch_threshold: float = 0.25,
    family_conf_threshold: float = 0.58,
    near_scale: float = 2.5,
    support_scale: float = 5.0,
    far_scale: float = 8.0,
    ambiguity_scale: float = 1.10,
    source_jump_scale: float = 7.0,
    face_semantic_l1: float = 1.35,
) -> dict:
    data = np.load(input_path, allow_pickle=True)
    source_points = data["source_points"].astype(np.float64)
    target_points = data["target_points"].astype(np.float64)
    source_weights = data["source_weights"].astype(np.float64)
    influence_names = data["influence_names"]

    source_faces = faces_from_flat(
        data["source_face_counts"], data["source_face_offsets"], data["source_face_vertices"]
    )
    target_faces = faces_from_flat(
        data["target_face_counts"], data["target_face_offsets"], data["target_face_vertices"]
    )
    source_tris = triangulate(source_faces)
    target_tris = triangulate(target_faces)
    target_edges = build_edges(target_faces)

    source_normals = vertex_normals(source_points, source_tris)
    target_normals = vertex_normals(target_points, target_tris)
    source_tri_normals = triangle_normals(source_points, source_tris)

    target_edge_med = edge_median(target_points, target_edges)
    source_edge_med = edge_median(source_points, build_edges(source_faces))
    near_distance = max(0.65, target_edge_med * near_scale)
    support_distance = max(1.50, target_edge_med * support_scale)
    far_distance = max(2.75, target_edge_med * far_scale)
    ambiguity_band = max(0.20, target_edge_med * ambiguity_scale)
    source_jump_threshold = max(2.25, source_edge_med * source_jump_scale, target_edge_med * source_jump_scale)
    bbox_expand = support_distance

    influence_family_matrix, influence_family_labels = family_matrix(influence_names)
    source_family_weights = source_weights @ influence_family_matrix
    source_family_sum = source_family_weights.sum(axis=1, keepdims=True)
    source_family_sum[source_family_sum == 0.0] = 1.0
    source_family_weights = source_family_weights / source_family_sum
    source_vertex_family = np.argmax(source_family_weights, axis=1).astype(np.int16)
    source_vertex_family_conf = np.max(source_family_weights, axis=1)

    tri_centroids = (
        source_points[source_tris[:, 0]]
        + source_points[source_tris[:, 1]]
        + source_points[source_tris[:, 2]]
    ) / 3.0
    tri_tree = cKDTree(tri_centroids)
    k = min(candidate_k, len(source_tris))

    n = len(target_points)
    best_tri = np.zeros(n, dtype=np.int32)
    best_dist = np.zeros(n, dtype=np.float64)
    best_point = np.zeros((n, 3), dtype=np.float64)
    best_bary = np.zeros((n, 3), dtype=np.float64)
    best_family_weights = np.zeros((n, len(FAMILIES)), dtype=np.float64)
    best_family = np.zeros(n, dtype=np.int16)
    best_family_conf = np.zeros(n, dtype=np.float64)
    normal_dot = np.zeros(n, dtype=np.float64)
    candidate_family_count = np.zeros(n, dtype=np.int16)
    candidate_family_margin = np.zeros(n, dtype=np.float64)

    _, tri_candidates = tri_tree.query(target_points, k=k)
    if k == 1:
        tri_candidates = tri_candidates[:, None]

    for i in range(n):
        cand = tri_candidates[i]
        tris = source_tris[cand]
        a = source_points[tris[:, 0]]
        b = source_points[tris[:, 1]]
        c = source_points[tris[:, 2]]
        points, bary, dist2 = closest_point_tri_batch(target_points[i], a, b, c)
        tri_families = (
            bary[:, 0:1] * source_family_weights[tris[:, 0]]
            + bary[:, 1:2] * source_family_weights[tris[:, 1]]
            + bary[:, 2:3] * source_family_weights[tris[:, 2]]
        )
        tri_family_sum = tri_families.sum(axis=1, keepdims=True)
        tri_family_sum[tri_family_sum == 0.0] = 1.0
        tri_families = tri_families / tri_family_sum
        tri_family_conf = np.max(tri_families, axis=1)
        cand_normal_dot = np.clip(source_tri_normals[cand] @ target_normals[i], -1.0, 1.0)
        if selection_mode == "energy":
            dist = np.sqrt(np.maximum(dist2, 0.0))
            score = dist / max(near_distance, 1e-8)
            score += float(normal_weight) * ((1.0 - cand_normal_dot) * 0.5)
            score += float(family_purity_weight) * (1.0 - tri_family_conf)
            order = np.argsort(score)
        else:
            order = np.argsort(dist2)
        best_local = int(order[0])
        chosen_tri = int(cand[best_local])
        best_tri[i] = chosen_tri
        best_dist[i] = float(math.sqrt(max(dist2[best_local], 0.0)))
        best_point[i] = points[best_local]
        best_bary[i] = bary[best_local]

        fam = tri_families[best_local].copy()
        fam_sum = float(fam.sum())
        if fam_sum > 0.0:
            fam = fam / fam_sum
        best_family_weights[i] = fam
        best_family[i] = int(np.argmax(fam))
        best_family_conf[i] = float(np.max(fam))
        normal_dot[i] = float(cand_normal_dot[best_local])

        band = best_dist[i] + ambiguity_band
        family_scores: dict[int, float] = {}
        for local_idx in order[: min(k, 24)]:
            dist = math.sqrt(max(float(dist2[local_idx]), 0.0))
            if dist > band:
                continue
            tri2 = source_tris[int(cand[local_idx])]
            bw = bary[local_idx]
            fam2 = tri_families[local_idx]
            fam_idx = int(np.argmax(fam2))
            fam_conf = float(np.max(fam2))
            if fam_conf < 0.50:
                continue
            prev = family_scores.get(fam_idx, 1e9)
            family_scores[fam_idx] = min(prev, dist)
        candidate_family_count[i] = len(family_scores)
        if len(family_scores) >= 2:
            sorted_dist = sorted(family_scores.values())
            candidate_family_margin[i] = float(sorted_dist[1] - sorted_dist[0])
        else:
            candidate_family_margin[i] = 1e9

        if i and i % 5000 == 0:
            print(f"[v082] projected {i}/{n}")

    min_bbox = data["source_bbox"][:3].astype(np.float64) - bbox_expand
    max_bbox = data["source_bbox"][3:].astype(np.float64) + bbox_expand
    outside_bbox = np.any((target_points < min_bbox) | (target_points > max_bbox), axis=1)
    unsupported = outside_bbox | (best_dist > far_distance)

    distance_low = (~unsupported) & (best_dist > near_distance)
    normal_mismatch = (~unsupported) & (normal_dot < normal_mismatch_threshold)
    semantic_ambiguous = (
        (~unsupported)
        & (candidate_family_count >= 2)
        & (candidate_family_margin < ambiguity_band * 0.75)
    )
    low_family_conf = (~unsupported) & (best_family_conf < family_conf_threshold)

    source_pair_distance = np.linalg.norm(best_point[target_edges[:, 0]] - best_point[target_edges[:, 1]], axis=1)
    target_edge_len = np.linalg.norm(target_points[target_edges[:, 0]] - target_points[target_edges[:, 1]], axis=1)
    edge_small = target_edge_len < (target_edge_med * 3.0)
    both_supported = (~unsupported[target_edges[:, 0]]) & (~unsupported[target_edges[:, 1]])
    family_change = best_family[target_edges[:, 0]] != best_family[target_edges[:, 1]]
    both_confident = (best_family_conf[target_edges[:, 0]] > 0.66) & (
        best_family_conf[target_edges[:, 1]] > 0.66
    )
    source_jump = source_pair_distance > source_jump_threshold
    topology_edge_risk = both_supported & edge_small & (source_jump | (family_change & both_confident))
    topology_discontinuity = np.zeros(n, dtype=bool)
    for a, b in target_edges[topology_edge_risk]:
        topology_discontinuity[int(a)] = True
        topology_discontinuity[int(b)] = True

    face_semantic_discontinuity = np.zeros(n, dtype=bool)
    face_risk_count = 0
    for face in target_faces:
        supported_ids = face[~unsupported[face]]
        if len(supported_ids) < 3:
            continue
        fam_rows = best_family_weights[supported_ids]
        if len(fam_rows) <= 1:
            continue
        max_l1 = float(np.max(np.abs(fam_rows[:, None, :] - fam_rows[None, :, :]).sum(axis=2)))
        fam_unique = len(set(int(x) for x in best_family[supported_ids]))
        if max_l1 > face_semantic_l1 or fam_unique >= 3:
            face_semantic_discontinuity[supported_ids] = True
            face_risk_count += 1

    low_confidence = (
        (~unsupported)
        & (
            distance_low
            | normal_mismatch
            | semantic_ambiguous
            | low_family_conf
            | topology_discontinuity
            | face_semantic_discontinuity
        )
    )
    high_confidence = (~unsupported) & (~low_confidence)

    confidence = np.ones(n, dtype=np.float64)
    confidence -= np.minimum(best_dist / max(near_distance, 1e-8), 1.0) * 0.28
    confidence -= np.maximum(0.0, 0.35 - normal_dot) * 0.35
    confidence -= semantic_ambiguous.astype(np.float64) * 0.24
    confidence -= low_family_conf.astype(np.float64) * 0.20
    confidence -= topology_discontinuity.astype(np.float64) * 0.18
    confidence -= face_semantic_discontinuity.astype(np.float64) * 0.12
    confidence[unsupported] = 0.0
    confidence = np.clip(confidence, 0.0, 1.0)

    flags = {
        "unsupported": unsupported,
        "distance_low": distance_low,
        "normal_mismatch": normal_mismatch,
        "semantic_ambiguous": semantic_ambiguous,
        "low_family_conf": low_family_conf,
        "topology_discontinuity": topology_discontinuity,
        "face_semantic_discontinuity": face_semantic_discontinuity,
        "low_confidence": low_confidence,
        "high_confidence": high_confidence,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    npz_path = output_dir / f"{output_prefix}_correspondence_validation.npz"
    np.savez_compressed(
        npz_path,
        best_tri=best_tri,
        best_dist=best_dist,
        best_point=best_point,
        best_bary=best_bary,
        best_family=best_family,
        best_family_conf=best_family_conf,
        best_family_weights=best_family_weights,
        normal_dot=normal_dot,
        candidate_family_count=candidate_family_count,
        candidate_family_margin=candidate_family_margin,
        confidence=confidence,
        unsupported=unsupported,
        low_confidence=low_confidence,
        high_confidence=high_confidence,
        distance_low=distance_low,
        normal_mismatch=normal_mismatch,
        semantic_ambiguous=semantic_ambiguous,
        low_family_conf=low_family_conf,
        topology_discontinuity=topology_discontinuity,
        face_semantic_discontinuity=face_semantic_discontinuity,
        source_tris=source_tris,
        families=np.asarray(FAMILIES, dtype=object),
    )

    low_indices = np.where(low_confidence)[0]
    unsupported_indices = np.where(unsupported)[0]
    strict_indices = np.where(
        (~unsupported)
        & (normal_mismatch | semantic_ambiguous | topology_discontinuity | face_semantic_discontinuity)
    )[0]

    csv_path = output_dir / f"{output_prefix}_low_confidence_vertices.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "target_vertex",
                "confidence",
                "best_dist",
                "normal_dot",
                "family",
                "family_conf",
                "candidate_family_count",
                "candidate_family_margin",
                "reasons",
            ]
        )
        order = sorted(low_indices.tolist(), key=lambda idx: (confidence[idx], -best_dist[idx]))
        for idx in order:
            writer.writerow(
                [
                    int(idx),
                    f"{confidence[idx]:.6f}",
                    f"{best_dist[idx]:.6f}",
                    f"{normal_dot[idx]:.6f}",
                    FAMILIES[int(best_family[idx])],
                    f"{best_family_conf[idx]:.6f}",
                    int(candidate_family_count[idx]),
                    f"{candidate_family_margin[idx]:.6f}",
                    reason_string(idx, flags),
                ]
            )

    set_indices = {
        "unsupported": np.where(unsupported)[0].astype(int).tolist(),
        "low_confidence_actionable": np.where(low_confidence)[0].astype(int).tolist(),
        "strict_block": strict_indices.astype(int).tolist(),
        "distance_low": np.where(distance_low)[0].astype(int).tolist(),
        "normal_mismatch": np.where(normal_mismatch)[0].astype(int).tolist(),
        "semantic_ambiguous": np.where(semantic_ambiguous)[0].astype(int).tolist(),
        "low_family_conf": np.where(low_family_conf)[0].astype(int).tolist(),
        "topology_discontinuity": np.where(topology_discontinuity)[0].astype(int).tolist(),
        "face_semantic_discontinuity": np.where(face_semantic_discontinuity)[0].astype(int).tolist(),
        "high_confidence_supported": np.where(high_confidence)[0].astype(int).tolist(),
    }
    sets_path = output_dir / f"{output_prefix}_maya_diagnostic_sets.json"
    sets_payload = {
        "status": "SUCCESS",
        "target_mesh": "A",
        "source_mesh": "M_Head_base",
        "default_selection": "low_confidence_actionable",
        "sets": set_indices,
    }
    sets_path.write_text(json.dumps(sets_payload, ensure_ascii=False), encoding="utf-8")

    summary = {
        "status": "SUCCESS",
        "input_path": str(input_path),
        "output_npz": str(npz_path),
        "output_csv": str(csv_path),
        "output_maya_sets": str(sets_path),
        "source_vertex_count": int(len(source_points)),
        "target_vertex_count": int(len(target_points)),
        "source_triangle_count": int(len(source_tris)),
        "target_face_count": int(len(target_faces)),
        "thresholds": {
            "target_edge_median": target_edge_med,
            "source_edge_median": source_edge_med,
            "near_distance": near_distance,
            "support_distance": support_distance,
            "far_distance": far_distance,
            "ambiguity_band": ambiguity_band,
            "source_jump_threshold": source_jump_threshold,
            "bbox_expand": bbox_expand,
            "candidate_k": int(candidate_k),
            "selection_mode": selection_mode,
            "normal_weight": float(normal_weight),
            "family_purity_weight": float(family_purity_weight),
            "normal_mismatch_threshold": float(normal_mismatch_threshold),
            "family_conf_threshold": float(family_conf_threshold),
            "near_scale": float(near_scale),
            "support_scale": float(support_scale),
            "far_scale": float(far_scale),
            "ambiguity_scale": float(ambiguity_scale),
            "source_jump_scale": float(source_jump_scale),
            "face_semantic_l1": float(face_semantic_l1),
        },
        "counts": {
            "unsupported": int(unsupported.sum()),
            "supported": int((~unsupported).sum()),
            "low_confidence_actionable": int(low_confidence.sum()),
            "strict_block": int(len(strict_indices)),
            "high_confidence": int(high_confidence.sum()),
            "distance_low": int(distance_low.sum()),
            "normal_mismatch": int(normal_mismatch.sum()),
            "semantic_ambiguous": int(semantic_ambiguous.sum()),
            "low_family_conf": int(low_family_conf.sum()),
            "topology_discontinuity": int(topology_discontinuity.sum()),
            "face_semantic_discontinuity": int(face_semantic_discontinuity.sum()),
            "face_semantic_risk_face_count": int(face_risk_count),
        },
        "samples": {
            "unsupported": summarize_indices(unsupported_indices),
            "low_confidence_actionable": summarize_indices(low_indices),
            "strict_block": summarize_indices(strict_indices),
            "normal_mismatch": summarize_indices(np.where(normal_mismatch)[0]),
            "semantic_ambiguous": summarize_indices(np.where(semantic_ambiguous)[0]),
            "topology_discontinuity": summarize_indices(np.where(topology_discontinuity)[0]),
            "face_semantic_discontinuity": summarize_indices(np.where(face_semantic_discontinuity)[0]),
        },
        "families": FAMILIES,
        "influence_family_counts": {
            family: int(sum(1 for label in influence_family_labels if label == family))
            for family in FAMILIES
        },
        "default_maya_selection": "low_confidence_actionable",
        "note": "Unsupported body/torso vertices are reported but should not be selected by default.",
    }
    summary_path = output_dir / f"{output_prefix}_correspondence_validation_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(INFO_DIR))
    parser.add_argument("--output-prefix", default="v082")
    parser.add_argument("--candidate-k", type=int, default=48)
    parser.add_argument("--selection-mode", choices=["distance", "energy"], default="distance")
    parser.add_argument("--normal-weight", type=float, default=0.0)
    parser.add_argument("--family-purity-weight", type=float, default=0.0)
    parser.add_argument("--normal-mismatch-threshold", type=float, default=0.25)
    parser.add_argument("--family-conf-threshold", type=float, default=0.58)
    parser.add_argument("--near-scale", type=float, default=2.5)
    parser.add_argument("--support-scale", type=float, default=5.0)
    parser.add_argument("--far-scale", type=float, default=8.0)
    parser.add_argument("--ambiguity-scale", type=float, default=1.10)
    parser.add_argument("--source-jump-scale", type=float, default=7.0)
    parser.add_argument("--face-semantic-l1", type=float, default=1.35)
    args = parser.parse_args()
    run_validator(
        Path(args.input),
        Path(args.output_dir),
        args.candidate_k,
        output_prefix=args.output_prefix,
        selection_mode=args.selection_mode,
        normal_weight=args.normal_weight,
        family_purity_weight=args.family_purity_weight,
        normal_mismatch_threshold=args.normal_mismatch_threshold,
        family_conf_threshold=args.family_conf_threshold,
        near_scale=args.near_scale,
        support_scale=args.support_scale,
        far_scale=args.far_scale,
        ambiguity_scale=args.ambiguity_scale,
        source_jump_scale=args.source_jump_scale,
        face_semantic_l1=args.face_semantic_l1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
