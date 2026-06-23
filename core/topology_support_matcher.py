"""
Topology-aware support-domain matcher for deformation inheritance.

This module intentionally sits below Maya/Blender code.  It solves the
specific failure mode where two regions are close in Euclidean space but far
on the mesh graph, such as upper/lower lips or adjacent fingers.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from typing import Iterable, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class SupportIsland:
    island_id: int
    family: str
    family_index: int
    vertex_indices: np.ndarray
    mean_score: float
    centroid: np.ndarray
    radius: float


@dataclass
class TransferResult:
    weights: np.ndarray
    labels: list[str]
    label_indices: np.ndarray
    confidence: np.ndarray
    source_indices: np.ndarray
    geodesic_distance: np.ndarray
    seed_indices: np.ndarray
    diagnostics: dict


@dataclass
class FieldTransferResult:
    values: np.ndarray
    labels: list[str]
    label_indices: np.ndarray
    confidence: np.ndarray
    source_indices: np.ndarray
    geodesic_distance: np.ndarray
    seed_indices: np.ndarray
    diagnostics: dict


DEFAULT_DEFORMATION_FAMILY_RULES = {
    "upper_lip": ["uplip"],
    "lower_lip": ["lolip"],
    "jaw": ["jaw", "chin"],
    "cheek": ["cheek", "nasolabial"],
    "head": ["head", "neck"],
}


def normalize_rows(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    out = np.maximum(values, 0.0).copy()
    sums = out.sum(axis=1, keepdims=True)
    mask = sums[:, 0] > 0.0
    out[mask] /= sums[mask]
    return out


def compute_family_scores(
    weights: np.ndarray,
    joint_names: Sequence[str],
    family_rules: Mapping[str, Sequence[str]],
) -> tuple[list[str], np.ndarray, dict[str, list[int]]]:
    weights = np.asarray(weights, dtype=np.float64)
    joint_lut = []
    for name in joint_names:
        # Maya influence names may be full DAG paths. Owner tokens must match the
        # actual influence leaf, not ancestor groups such as M_Head_A_zero.
        leaf = str(name).split("|")[-1].split(":")[-1].lower()
        joint_lut.append(leaf)
    families = list(family_rules.keys())
    scores = np.zeros((weights.shape[0], len(families)), dtype=np.float64)
    family_joint_indices: dict[str, list[int]] = {}

    for family_index, family in enumerate(families):
        tokens = [str(token).lower() for token in family_rules[family]]
        matched: list[int] = []
        for joint_index, joint_name in enumerate(joint_lut):
            if any(token == joint_name or token in joint_name for token in tokens):
                matched.append(joint_index)
        family_joint_indices[family] = matched
        if matched:
            scores[:, family_index] = weights[:, matched].sum(axis=1)

    return families, scores, family_joint_indices


def build_weighted_adjacency(vertices: np.ndarray, faces: np.ndarray) -> list[list[tuple[int, float]]]:
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    adjacency: list[dict[int, float]] = [dict() for _ in range(vertices.shape[0])]
    if faces.size == 0:
        return [[] for _ in range(vertices.shape[0])]

    for face in faces:
        valid = [int(v) for v in face if 0 <= int(v) < vertices.shape[0]]
        if len(valid) < 2:
            continue
        for i, a in enumerate(valid):
            b = valid[(i + 1) % len(valid)]
            if a == b:
                continue
            length = float(np.linalg.norm(vertices[a] - vertices[b]))
            if length <= 0.0:
                length = 1e-12
            old = adjacency[a].get(b)
            if old is None or length < old:
                adjacency[a][b] = length
                adjacency[b][a] = length
    return [list(row.items()) for row in adjacency]


def _connected_components_for_mask(faces: np.ndarray, mask: np.ndarray) -> list[np.ndarray]:
    mask = np.asarray(mask, dtype=bool)
    faces = np.asarray(faces, dtype=np.int64)
    parent = np.arange(mask.shape[0], dtype=np.int64)
    used = np.zeros(mask.shape[0], dtype=bool)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    if faces.size:
        for face in faces:
            valid = [int(v) for v in face if 0 <= int(v) < mask.shape[0] and mask[int(v)]]
            if not valid:
                continue
            used[valid] = True
            head = valid[0]
            for value in valid[1:]:
                union(head, value)

    isolated = np.where(mask & ~used)[0]
    groups: dict[int, list[int]] = {}
    for vertex_index in np.where(mask & used)[0]:
        groups.setdefault(find(int(vertex_index)), []).append(int(vertex_index))

    components = [np.asarray(items, dtype=np.int64) for items in groups.values()]
    components.extend(np.asarray([int(v)], dtype=np.int64) for v in isolated)
    components.sort(key=lambda arr: (-int(arr.shape[0]), int(arr.min()) if arr.size else 0))
    return components


def extract_support_islands(
    vertices: np.ndarray,
    faces: np.ndarray,
    family_scores: np.ndarray,
    families: Sequence[str],
    threshold: float = 0.5,
    min_vertices: int = 3,
) -> list[SupportIsland]:
    vertices = np.asarray(vertices, dtype=np.float64)
    islands: list[SupportIsland] = []
    island_id = 0

    for family_index, family in enumerate(families):
        mask = np.asarray(family_scores[:, family_index] >= threshold, dtype=bool)
        for vertex_indices in _connected_components_for_mask(faces, mask):
            if vertex_indices.shape[0] < min_vertices:
                continue
            pts = vertices[vertex_indices]
            centroid = pts.mean(axis=0)
            radius = float(np.max(np.linalg.norm(pts - centroid, axis=1))) if pts.size else 0.0
            islands.append(
                SupportIsland(
                    island_id=island_id,
                    family=str(family),
                    family_index=int(family_index),
                    vertex_indices=vertex_indices,
                    mean_score=float(np.mean(family_scores[vertex_indices, family_index])),
                    centroid=centroid,
                    radius=radius,
                )
            )
            island_id += 1
    return islands


def propagate_labels_by_topology(
    vertices: np.ndarray,
    faces: np.ndarray,
    seed_indices: Sequence[int],
    seed_label_indices: Sequence[int],
    seed_confidence: Sequence[float] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    vertices = np.asarray(vertices, dtype=np.float64)
    seed_indices_arr = np.asarray(seed_indices, dtype=np.int64)
    seed_labels_arr = np.asarray(seed_label_indices, dtype=np.int64)

    n_vertices = int(vertices.shape[0])
    label_indices = np.full(n_vertices, -1, dtype=np.int64)
    distances = np.full(n_vertices, np.inf, dtype=np.float64)
    nearest_seed = np.full(n_vertices, -1, dtype=np.int64)
    confidence = np.zeros(n_vertices, dtype=np.float64)

    if seed_indices_arr.size == 0:
        return label_indices, distances, nearest_seed, confidence

    if seed_confidence is None:
        seed_conf = np.ones(seed_indices_arr.shape[0], dtype=np.float64)
    else:
        seed_conf = np.asarray(seed_confidence, dtype=np.float64)

    adjacency = build_weighted_adjacency(vertices, faces)
    heap: list[tuple[float, int, int, int, float]] = []

    for order, vertex_index in enumerate(seed_indices_arr):
        vi = int(vertex_index)
        if vi < 0 or vi >= n_vertices:
            continue
        label = int(seed_labels_arr[order])
        conf = float(np.clip(seed_conf[order], 0.0, 1.0))
        if 0.0 < distances[vi]:
            distances[vi] = 0.0
            label_indices[vi] = label
            nearest_seed[vi] = vi
            confidence[vi] = conf
            heapq.heappush(heap, (0.0, vi, label, vi, conf))

    while heap:
        dist, vertex_index, label, seed_index, seed_conf_value = heapq.heappop(heap)
        if dist != distances[vertex_index] or label != label_indices[vertex_index]:
            continue
        for neighbor, edge_length in adjacency[vertex_index]:
            nd = dist + edge_length
            if nd < distances[neighbor]:
                distances[neighbor] = nd
                label_indices[neighbor] = label
                nearest_seed[neighbor] = seed_index
                confidence[neighbor] = seed_conf_value
                heapq.heappush(heap, (nd, neighbor, label, seed_index, seed_conf_value))

    finite = distances[np.isfinite(distances)]
    if finite.size:
        falloff = max(float(np.percentile(finite, 75)), 1e-8)
        confidence *= np.exp(-distances / (falloff * 3.0))
    confidence[~np.isfinite(distances)] = 0.0
    return label_indices, distances, nearest_seed, np.clip(confidence, 0.0, 1.0)


class TopologySupportMatcher:
    def __init__(
        self,
        source_vertices: np.ndarray,
        source_faces: np.ndarray,
        source_weights: np.ndarray,
        joint_names: Sequence[str],
        family_rules: Mapping[str, Sequence[str]],
        source_normals: np.ndarray | None = None,
        support_threshold: float = 0.5,
        min_island_vertices: int = 3,
    ) -> None:
        self.source_vertices = np.asarray(source_vertices, dtype=np.float64)
        self.source_faces = np.asarray(source_faces, dtype=np.int64)
        self.source_weights = normalize_rows(np.asarray(source_weights, dtype=np.float64))
        self.joint_names = list(joint_names)
        self.family_rules = {str(k): list(v) for k, v in family_rules.items()}
        self.source_normals = None if source_normals is None else np.asarray(source_normals, dtype=np.float64)

        self.families, self.family_scores, self.family_joint_indices = compute_family_scores(
            self.source_weights, self.joint_names, self.family_rules
        )
        self.islands = extract_support_islands(
            self.source_vertices,
            self.source_faces,
            self.family_scores,
            self.families,
            threshold=support_threshold,
            min_vertices=min_island_vertices,
        )
        self._candidate_indices_by_family = self._build_candidate_indices(support_threshold)
        self._edge_scale = self._estimate_edge_scale()

    def _estimate_edge_scale(self) -> float:
        edges = []
        for row_index, row in enumerate(build_weighted_adjacency(self.source_vertices, self.source_faces)):
            for neighbor, length in row:
                if neighbor > row_index:
                    edges.append(length)
        if edges:
            return max(float(np.median(edges)), 1e-8)
        bbox = self.source_vertices.max(axis=0) - self.source_vertices.min(axis=0)
        return max(float(np.linalg.norm(bbox)) * 0.01, 1e-8)

    def _build_candidate_indices(self, threshold: float) -> dict[int, np.ndarray]:
        candidates: dict[int, np.ndarray] = {}
        for family_index in range(len(self.families)):
            mask = self.family_scores[:, family_index] >= threshold
            indices = np.where(mask)[0].astype(np.int64)
            if indices.size == 0:
                indices = np.argsort(-self.family_scores[:, family_index])[: max(1, min(32, self.source_vertices.shape[0]))]
            candidates[family_index] = np.asarray(indices, dtype=np.int64)
        return candidates

    def _parse_extra_seeds(
        self,
        target_seed_labels: Mapping[int, str] | Sequence[tuple[int, str]] | None,
    ) -> tuple[list[int], list[int], list[float]]:
        if not target_seed_labels:
            return [], [], []
        if isinstance(target_seed_labels, Mapping):
            items: Iterable[tuple[int, str]] = target_seed_labels.items()
        else:
            items = target_seed_labels

        family_to_index = {family: i for i, family in enumerate(self.families)}
        seed_indices: list[int] = []
        seed_label_indices: list[int] = []
        seed_confidence: list[float] = []
        for item in items:
            vertex_index = int(item[0])
            family = str(item[1])
            if family not in family_to_index:
                continue
            seed_indices.append(vertex_index)
            seed_label_indices.append(family_to_index[family])
            seed_confidence.append(1.0)
        return seed_indices, seed_label_indices, seed_confidence

    def _auto_seed_labels(
        self,
        target_vertices: np.ndarray,
        target_normals: np.ndarray | None,
        source_motion: np.ndarray | None,
        target_motion: np.ndarray | None,
        k: int,
        motion_weight: float,
        normal_weight: float,
        min_margin: float,
    ) -> tuple[list[int], list[int], list[float], dict]:
        from scipy.spatial import cKDTree

        candidate_indices = np.unique(np.concatenate(list(self._candidate_indices_by_family.values())))
        if candidate_indices.size == 0:
            return [], [], [], {"auto_seed_count": 0}

        tree = cKDTree(self.source_vertices[candidate_indices])
        query_k = min(max(int(k), 1), candidate_indices.size)
        dists, local_idx = tree.query(target_vertices, k=query_k)
        if query_k == 1:
            dists = dists[:, np.newaxis]
            local_idx = local_idx[:, np.newaxis]

        scale = max(self._edge_scale * 3.0, 1e-8)
        seed_indices: list[int] = []
        seed_label_indices: list[int] = []
        seed_confidence: list[float] = []

        for target_index in range(target_vertices.shape[0]):
            family_best: dict[int, float] = {}
            for rank in range(query_k):
                src_index = int(candidate_indices[int(local_idx[target_index, rank])])
                src_family = int(np.argmax(self.family_scores[src_index]))
                score = float(dists[target_index, rank] / scale)
                score += 1.0 - float(self.family_scores[src_index, src_family])
                if target_normals is not None and self.source_normals is not None:
                    dot = float(np.dot(target_normals[target_index], self.source_normals[src_index]))
                    score += normal_weight * max(0.0, 1.0 - dot)
                if source_motion is not None and target_motion is not None:
                    delta = target_motion[target_index] - source_motion[src_index]
                    score += motion_weight * float(np.linalg.norm(delta)) / scale
                old = family_best.get(src_family)
                if old is None or score < old:
                    family_best[src_family] = score

            if not family_best:
                continue
            ordered = sorted(family_best.items(), key=lambda item: item[1])
            best_family, best_score = ordered[0]
            second_score = ordered[1][1] if len(ordered) > 1 else best_score + 1.0
            margin = second_score - best_score
            if margin < min_margin:
                continue
            confidence = 1.0 / (1.0 + max(best_score, 0.0))
            confidence *= min(1.0, margin / max(min_margin * 2.0, 1e-8))
            seed_indices.append(target_index)
            seed_label_indices.append(int(best_family))
            seed_confidence.append(float(np.clip(confidence, 0.0, 1.0)))

        return seed_indices, seed_label_indices, seed_confidence, {"auto_seed_count": len(seed_indices)}

    def transfer_weights(
        self,
        target_vertices: np.ndarray,
        target_faces: np.ndarray,
        target_normals: np.ndarray | None = None,
        target_seed_labels: Mapping[int, str] | Sequence[tuple[int, str]] | None = None,
        source_motion: np.ndarray | None = None,
        target_motion: np.ndarray | None = None,
        k: int = 8,
        auto_seed_k: int = 12,
        auto_seed: bool = True,
        min_seed_margin: float = 0.25,
        motion_weight: float = 2.0,
        normal_weight: float = 0.25,
    ) -> TransferResult:
        field_result = self.transfer_values(
            self.source_weights,
            target_vertices,
            target_faces,
            target_normals=target_normals,
            target_seed_labels=target_seed_labels,
            source_motion=source_motion,
            target_motion=target_motion,
            k=k,
            auto_seed_k=auto_seed_k,
            auto_seed=auto_seed,
            min_seed_margin=min_seed_margin,
            motion_weight=motion_weight,
            normal_weight=normal_weight,
        )
        return TransferResult(
            weights=normalize_rows(field_result.values),
            labels=field_result.labels,
            label_indices=field_result.label_indices,
            confidence=field_result.confidence,
            source_indices=field_result.source_indices,
            geodesic_distance=field_result.geodesic_distance,
            seed_indices=field_result.seed_indices,
            diagnostics=field_result.diagnostics,
        )

    def transfer_values(
        self,
        source_values: np.ndarray,
        target_vertices: np.ndarray,
        target_faces: np.ndarray,
        target_normals: np.ndarray | None = None,
        target_seed_labels: Mapping[int, str] | Sequence[tuple[int, str]] | None = None,
        source_motion: np.ndarray | None = None,
        target_motion: np.ndarray | None = None,
        k: int = 8,
        auto_seed_k: int = 12,
        auto_seed: bool = True,
        min_seed_margin: float = 0.25,
        motion_weight: float = 2.0,
        normal_weight: float = 0.25,
    ) -> FieldTransferResult:
        """Transfer any per-source-vertex field through the topology support labels.

        Skin weights are only one field. BlendShape deltas, residual motion, masks,
        and diagnostics need the same semantic/topological guard; otherwise close
        regions such as upper/lower lips can use different correspondence logic.
        """
        from scipy.spatial import cKDTree

        source_values = np.asarray(source_values, dtype=np.float64)
        squeeze = False
        if source_values.ndim == 1:
            source_values = source_values[:, np.newaxis]
            squeeze = True
        if source_values.shape[0] != self.source_vertices.shape[0]:
            raise ValueError(
                f"source_values vertex count {source_values.shape[0]} != "
                f"source_vertices {self.source_vertices.shape[0]}"
            )

        target_vertices = np.asarray(target_vertices, dtype=np.float64)
        target_faces = np.asarray(target_faces, dtype=np.int64)
        if target_normals is not None:
            target_normals = np.asarray(target_normals, dtype=np.float64)
        if source_motion is not None:
            source_motion = np.asarray(source_motion, dtype=np.float64)
        if target_motion is not None:
            target_motion = np.asarray(target_motion, dtype=np.float64)

        manual_seed_indices, manual_seed_labels, manual_seed_conf = self._parse_extra_seeds(target_seed_labels)
        if auto_seed:
            auto_seed_indices, auto_seed_labels, auto_seed_conf, auto_diag = self._auto_seed_labels(
                target_vertices,
                target_normals,
                source_motion,
                target_motion,
                auto_seed_k,
                motion_weight,
                normal_weight,
                min_seed_margin,
            )
        else:
            auto_seed_indices, auto_seed_labels, auto_seed_conf = [], [], []
            auto_diag = {"auto_seed_count": 0}

        seed_indices = manual_seed_indices + auto_seed_indices
        seed_label_indices = manual_seed_labels + auto_seed_labels
        seed_confidence = manual_seed_conf + auto_seed_conf

        label_indices, geo_dist, nearest_seed, label_confidence = propagate_labels_by_topology(
            target_vertices,
            target_faces,
            seed_indices,
            seed_label_indices,
            seed_confidence,
        )

        values = np.zeros((target_vertices.shape[0], source_values.shape[1]), dtype=np.float64)
        source_indices = np.full(target_vertices.shape[0], -1, dtype=np.int64)
        family_trees: dict[int, tuple[cKDTree, np.ndarray]] = {}
        scale = max(self._edge_scale * 3.0, 1e-8)

        for family_index, indices in self._candidate_indices_by_family.items():
            family_trees[family_index] = (cKDTree(self.source_vertices[indices]), indices)

        for target_index in range(target_vertices.shape[0]):
            family_index = int(label_indices[target_index])
            if family_index < 0 or family_index not in family_trees:
                family_index = self._fallback_family(target_vertices[target_index])
                label_indices[target_index] = family_index
                label_confidence[target_index] *= 0.25

            tree, indices = family_trees[family_index]
            query_k = min(max(int(k), 1), indices.shape[0])
            dists, local = tree.query(target_vertices[target_index], k=query_k)
            dists = np.atleast_1d(dists).astype(np.float64)
            local = np.atleast_1d(local).astype(np.int64)
            src_idx = indices[local]
            cost = dists / scale
            cost += 1.0 - self.family_scores[src_idx, family_index]
            if target_normals is not None and self.source_normals is not None:
                dots = self.source_normals[src_idx] @ target_normals[target_index]
                cost += normal_weight * np.maximum(0.0, 1.0 - dots)
            if source_motion is not None and target_motion is not None:
                delta = source_motion[src_idx] - target_motion[target_index]
                cost += motion_weight * np.linalg.norm(delta, axis=1) / scale

            blend = 1.0 / np.square(cost + 1e-6)
            blend_sum = float(blend.sum())
            if blend_sum <= 0.0 or not math.isfinite(blend_sum):
                best_rank = int(np.argmin(cost))
                values[target_index] = source_values[src_idx[best_rank]]
                source_indices[target_index] = int(src_idx[best_rank])
            else:
                blend /= blend_sum
                values[target_index] = blend @ source_values[src_idx]
                source_indices[target_index] = int(src_idx[int(np.argmax(blend))])

        if squeeze:
            values = values[:, 0]
        label_names = [self.families[i] if 0 <= int(i) < len(self.families) else "unknown" for i in label_indices]
        diagnostics = {
            "families": self.families,
            "support_island_count": len(self.islands),
            "support_islands": [
                {
                    "id": island.island_id,
                    "family": island.family,
                    "vertices": int(island.vertex_indices.shape[0]),
                    "mean_score": island.mean_score,
                    "radius": island.radius,
                }
                for island in self.islands
            ],
            "manual_seed_count": len(manual_seed_indices),
            **auto_diag,
            "propagated_count": int(np.sum(label_indices >= 0)),
            "low_confidence_count": int(np.sum(label_confidence < 0.35)),
        }
        return FieldTransferResult(
            values=values,
            labels=label_names,
            label_indices=label_indices,
            confidence=np.asarray(label_confidence, dtype=np.float64),
            source_indices=source_indices,
            geodesic_distance=geo_dist,
            seed_indices=nearest_seed,
            diagnostics=diagnostics,
        )

    def _fallback_family(self, point: np.ndarray) -> int:
        best_family = 0
        best_dist = np.inf
        for family_index, indices in self._candidate_indices_by_family.items():
            pts = self.source_vertices[indices]
            local_dist = np.min(np.linalg.norm(pts - point, axis=1))
            if local_dist < best_dist:
                best_dist = float(local_dist)
                best_family = int(family_index)
        return best_family
