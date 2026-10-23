"""Pure owner ranking for component-scoped skin inheritance."""

from __future__ import annotations

from difflib import SequenceMatcher
import math
import re
from typing import Any


class OwnershipAmbiguous(ValueError):
    """Raised when geometry and naming do not identify one safe source."""

    def __init__(self, message: str, candidates: list[dict[str, Any]]):
        super().__init__(message)
        self.candidates = candidates


def canonical_name(path: str) -> str:
    leaf = str(path).rsplit("|", 1)[-1].rsplit(":", 1)[-1]
    leaf = re.sub(r"(?i)(shape|orig|mesh|msh|geo)+$", "", leaf)
    return re.sub(r"[^a-z0-9]+", "", leaf.lower())


def _distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


def rank_owners(target: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank pre-measured sources without reading or changing Maya state."""
    target_name = canonical_name(target["name"])
    target_count = max(int(target["vertex_count"]), 1)
    target_diag = max(float(target["diagonal"]), 1e-6)
    ranked = []
    for source in sources:
        source_name = canonical_name(source["name"])
        name_score = SequenceMatcher(None, target_name, source_name).ratio()
        if target_name and target_name == source_name:
            name_score = 1.0
        source_count = max(int(source["vertex_count"]), 1)
        count_score = min(target_count, source_count) / max(target_count, source_count)
        source_diag = max(float(source["diagonal"]), 1e-6)
        size_score = math.exp(-abs(math.log(target_diag / source_diag)))
        center_scale = max(target_diag, source_diag)
        center_score = math.exp(
            -_distance(target["centroid"], source["centroid"]) / center_scale
        )
        surface_score = math.exp(-float(source["surface_distance"]) / target_diag)
        score = (
            0.25 * name_score
            + 0.20 * count_score
            + 0.10 * size_score
            + 0.10 * center_score
            + 0.35 * surface_score
        )
        ranked.append({
            "source": source["source"],
            "score": round(score, 6),
            "name_score": round(name_score, 6),
            "vertex_count_score": round(count_score, 6),
            "size_score": round(size_score, 6),
            "center_score": round(center_score, 6),
            "surface_score": round(surface_score, 6),
            "surface_distance": round(float(source["surface_distance"]), 6),
        })
    return sorted(ranked, key=lambda row: (-row["score"], row["source"]))


def choose_owner(target: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = rank_owners(target, sources)
    if not ranked:
        raise OwnershipAmbiguous("No skinned source mesh is available", [])
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    if best["score"] < 0.60:
        raise OwnershipAmbiguous("No source owner reached the minimum confidence", ranked[:5])
    gap = best["score"] - second["score"] if second else 1.0
    required_gap = 0.02 if best["name_score"] == 1.0 else 0.05
    if second and gap < required_gap:
        raise OwnershipAmbiguous("Source ownership is ambiguous", ranked[:5])
    return {**best, "score_gap": round(gap, 6), "candidates": ranked[:5]}
