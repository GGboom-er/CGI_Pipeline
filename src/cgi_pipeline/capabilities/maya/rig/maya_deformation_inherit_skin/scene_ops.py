"""Bounded Maya scene and artifact operations for skin inheritance."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import statistics
from typing import Any

from cgi_pipeline.core.path_guard import is_protected_path
from cgi_pipeline.paths import REPOSITORY_ROOT


def inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def _in_task_sandbox(path: Path) -> bool:
    root = Path(REPOSITORY_ROOT).resolve()
    return inside(path, root / "projects") or inside(path, root / "runs")


def output_paths(params: dict[str, Any], context) -> tuple[Path, Path, Path, Path | None]:
    reference = Path(params.get("reference_rig_path") or "").resolve()
    if not reference.is_file() or reference.suffix.lower() not in {".ma", ".mb"}:
        raise ValueError("reference_rig_path must be an existing .ma or .mb file")
    run_root = Path(context.run_dir).resolve() if context.run_dir else None
    source = Path(context.source_path).resolve() if context.source_path else None
    if params.get("info_dir"):
        info_dir = Path(params["info_dir"]).resolve()
    elif run_root:
        info_dir = run_root / ".info"
    elif source:
        info_dir = source.parent / ".info"
    else:
        raise ValueError("info_dir is required when no task run_dir or source_path exists")
    if is_protected_path(str(info_dir)) or not _in_task_sandbox(info_dir):
        raise ValueError("info_dir must stay inside the managed projects or runs sandbox")
    output = None
    if params.get("mode", "diagnose_only") == "apply_skin":
        if not params.get("output_path"):
            raise ValueError("output_path is required in apply_skin mode")
        output = Path(params["output_path"]).resolve()
        if output.suffix.lower() not in {".ma", ".mb"}:
            raise ValueError("output_path must end in .ma or .mb")
        if output.exists():
            raise ValueError("output_path must be a new file")
        if is_protected_path(str(output)) or not _in_task_sandbox(output):
            raise ValueError("output_path must stay inside the managed projects or runs sandbox")
        if output in {reference, source}:
            raise ValueError("output_path cannot overwrite a source scene")
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", context.asset_name or "skin_inherit")
    return reference, info_dir / f"{stem}_diagnostic.json", info_dir / f"{stem}_weights.npz", output


def strip_namespaces(path: str) -> str:
    return "|" + "|".join(part.rsplit(":", 1)[-1] for part in str(path).split("|") if part)


def long_path(cmds, node: str) -> str:
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        raise ValueError(f"Expected one exact Maya node: {node}")
    return str(matches[0])


def mesh_rows(cmds, group: str) -> list[dict[str, str]]:
    root = long_path(cmds, group)
    transforms = [root] + list(cmds.listRelatives(
        root, allDescendents=True, fullPath=True, type="transform"
    ) or [])
    rows = []
    for transform in sorted(set(transforms)):
        shapes = cmds.listRelatives(
            transform, shapes=True, noIntermediate=True, fullPath=True, type="mesh"
        ) or []
        if not shapes:
            continue
        if len(shapes) != 1:
            raise ValueError(f"Expected one visible mesh shape under: {transform}")
        rows.append({"transform": long_path(cmds, transform), "shape": long_path(cmds, shapes[0])})
    return rows


def skin_cluster(cmds, shape: str, required: bool = True) -> str:
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    clusters = cmds.ls(history, long=True, type="skinCluster") or []
    if not clusters and not required:
        return ""
    if len(clusters) != 1:
        raise ValueError(f"Expected exactly one skinCluster on: {shape}")
    return str(clusters[0])


def _dag_and_mesh(shape: str):
    from maya.api import OpenMaya as om

    selected = om.MSelectionList(); selected.add(shape)
    dag = selected.getDagPath(0)
    return dag, om.MFnMesh(dag)


def _metrics(indices: list[int], points) -> dict[str, Any]:
    xyz = [[float(points[index].x), float(points[index].y), float(points[index].z)] for index in indices]
    low = [min(row[axis] for row in xyz) for axis in range(3)]
    high = [max(row[axis] for row in xyz) for axis in range(3)]
    centroid = [sum(row[axis] for row in xyz) / len(xyz) for axis in range(3)]
    diagonal = math.sqrt(sum((high[axis] - low[axis]) ** 2 for axis in range(3)))
    step = max(len(indices) // 16, 1)
    return {
        "vertices": indices, "vertex_count": len(indices), "centroid": centroid,
        "diagonal": max(diagonal, 1e-6),
        "samples": [xyz[position] for position in range(0, len(indices), step)][:16],
    }


def components(shape: str) -> list[dict[str, Any]]:
    from maya.api import OpenMaya as om

    _dag, mesh = _dag_and_mesh(shape)
    points = mesh.getPoints(om.MSpace.kWorld)
    counts, vertices = mesh.getVertices()
    parent = list(range(mesh.numVertices))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]; value = parent[value]
        return value

    offset = 0
    for count in counts:
        face = [int(value) for value in vertices[offset:offset + count]]; offset += int(count)
        if not face:
            continue
        root = find(face[0])
        for value in face[1:]:
            other = find(value)
            if root != other:
                parent[other] = root
    groups: dict[int, list[int]] = {}
    for vertex in range(mesh.numVertices):
        groups.setdefault(find(vertex), []).append(vertex)
    return [_metrics(row, points) for row in sorted(groups.values(), key=lambda item: item[0])]


def source_metrics(shape: str) -> dict[str, Any]:
    from maya.api import OpenMaya as om

    indices = [value for component in components(shape) for value in component["vertices"]]
    _dag, mesh = _dag_and_mesh(shape)
    return _metrics(indices, mesh.getPoints(om.MSpace.kWorld))


def surface_distance(shape: str, samples: list[list[float]]) -> float:
    from maya.api import OpenMaya as om

    dag, _mesh = _dag_and_mesh(shape)
    intersector = om.MMeshIntersector(); intersector.create(dag.node(), dag.inclusiveMatrix())
    distances = []
    for sample in samples:
        point = om.MPoint(*sample)
        hit = intersector.getClosestPoint(point).point
        distances.append(point.distanceTo(om.MPoint(hit.x, hit.y, hit.z)))
    return float(statistics.median(distances))


def compact_vertices(shape: str, indices: list[int]) -> list[str]:
    ranges = []
    start = previous = indices[0]
    for value in indices[1:]:
        if value != previous + 1:
            ranges.append((start, previous)); start = value
        previous = value
    ranges.append((start, previous))
    return [f"{shape}.vtx[{a}]" if a == b else f"{shape}.vtx[{a}:{b}]" for a, b in ranges]


def skin_weights(skin: str, shape: str) -> tuple[list[str], list[float], int]:
    from maya.api import OpenMaya as om
    from maya.api import OpenMayaAnim as oma

    dag, mesh = _dag_and_mesh(shape)
    selected = om.MSelectionList(); selected.add(skin)
    skin_fn = oma.MFnSkinCluster(selected.getDependNode(0))
    component_fn = om.MFnSingleIndexedComponent()
    component = component_fn.create(om.MFn.kMeshVertComponent)
    component_fn.addElements(range(mesh.numVertices))
    values, count = skin_fn.getWeights(dag, component)
    influences = [path.fullPathName() for path in skin_fn.influenceObjects()]
    if int(count) != len(influences):
        raise RuntimeError("skinCluster influence order is inconsistent")
    return influences, [float(value) for value in values], mesh.numVertices


def set_skin_weights(skin: str, shape: str, values: list[float]) -> None:
    from maya.api import OpenMaya as om
    from maya.api import OpenMayaAnim as oma

    dag, mesh = _dag_and_mesh(shape)
    selected = om.MSelectionList(); selected.add(skin)
    skin_fn = oma.MFnSkinCluster(selected.getDependNode(0))
    count = len(skin_fn.influenceObjects())
    if len(values) != mesh.numVertices * count:
        raise ValueError("weight matrix dimensions do not match the target skinCluster")
    component_fn = om.MFnSingleIndexedComponent()
    component = component_fn.create(om.MFn.kMeshVertComponent)
    component_fn.addElements(range(mesh.numVertices))
    skin_fn.setWeights(dag, component, om.MIntArray(range(count)), om.MDoubleArray(values), True)


def _skin_shape(cmds, skin: str) -> str:
    geometry = cmds.skinCluster(skin, query=True, geometry=True) or []
    shapes = cmds.ls(geometry[0], long=True, type="mesh") if len(geometry) == 1 else []
    if len(shapes or []) != 1:
        raise ValueError(f"Expected one mesh geometry on skinCluster: {skin}")
    return str(shapes[0])


def local_influences(cmds, namespace: str, source_skin: str,
                     overrides: dict[str, str]) -> tuple[list[str], list[str]]:
    source, _values, _vertices = skin_weights(source_skin, _skin_shape(cmds, source_skin))
    local = [str(node) for node in (cmds.ls(type="joint", long=True) or []) if f"{namespace}:" not in str(node)]
    by_name: dict[str, list[str]] = {}
    for node in local:
        by_name.setdefault(strip_namespaces(node).rsplit("|", 1)[-1], []).append(node)
    mapped, issues = [], []
    for influence in source:
        stripped = strip_namespaces(influence); key = stripped.rsplit("|", 1)[-1]
        explicit = overrides.get(stripped, overrides.get(key, ""))
        matches = [long_path(cmds, explicit)] if explicit else by_name.get(key, [])
        if len(matches) != 1 or cmds.nodeType(matches[0]) != "joint":
            issues.append(f"{influence}: expected one local joint mapping, found {len(matches)}")
        else:
            mapped.append(matches[0])
    return mapped, issues


def make_proxy(cmds, source: dict[str, Any], mapped: list[str], prefix: str) -> dict[str, str]:
    source_influences, source_values, vertex_count = skin_weights(source["skin"], source["shape"])
    proxy = cmds.duplicate(source["transform"], returnRootsOnly=True, name=f"{prefix}proxy")[0]
    if cmds.listRelatives(proxy, parent=True, fullPath=True):
        proxy = (cmds.parent(proxy, world=True) or [proxy])[0]
    cmds.delete(proxy, constructionHistory=True)
    proxy_shape = (cmds.listRelatives(proxy, shapes=True, noIntermediate=True, fullPath=True) or [])[0]
    proxy_skin = cmds.skinCluster(
        *(mapped + [proxy]), toSelectedBones=True, normalizeWeights=1,
        maximumInfluences=max(len(mapped), 1), name=f"{proxy}_skin"
    )[0]
    target_influences, _empty, target_vertices = skin_weights(proxy_skin, proxy_shape)
    if target_vertices != vertex_count:
        raise RuntimeError("proxy geometry changed vertex count")
    target_index = {str(node): index for index, node in enumerate(target_influences)}
    source_to_target = [target_index[long_path(cmds, node)] for node in mapped]
    matrix = [0.0] * (vertex_count * len(target_influences))
    for vertex in range(vertex_count):
        for source_index, destination_index in enumerate(source_to_target):
            matrix[vertex * len(target_influences) + destination_index] = (
                source_values[vertex * len(source_influences) + source_index]
            )
    set_skin_weights(proxy_skin, proxy_shape, matrix)
    return {"transform": long_path(cmds, proxy), "shape": long_path(cmds, proxy_shape),
            "skin": long_path(cmds, proxy_skin)}


def ensure_target_skin(cmds, target: dict[str, Any], influences: list[str], maximum: int) -> str:
    skin = skin_cluster(cmds, target["shape"], required=False)
    if not skin:
        kwargs = {"toSelectedBones": True, "normalizeWeights": 1}
        if maximum:
            kwargs["maximumInfluences"] = maximum
        result = cmds.skinCluster(*(influences + [target["transform"]]), **kwargs) or []
        if len(result) != 1:
            raise RuntimeError("skinCluster did not create one target binding")
        return long_path(cmds, result[0])
    existing, _values, _count = skin_weights(skin, target["shape"])
    existing = {long_path(cmds, node) for node in existing}
    for influence in influences:
        resolved = long_path(cmds, influence)
        if resolved not in existing:
            cmds.skinCluster(skin, edit=True, addInfluence=resolved, weight=0.0)
    return skin


def prune(values: list[float], vertices: int, influences: int, maximum: int) -> list[float]:
    if not maximum or maximum >= influences:
        return values
    result = list(values)
    for vertex in range(vertices):
        offset = vertex * influences; row = result[offset:offset + influences]
        keep = set(sorted(range(influences), key=lambda index: row[index], reverse=True)[:maximum])
        total = sum(row[index] for index in keep)
        if total <= 0:
            raise RuntimeError(f"Vertex {vertex} has zero total skin weight")
        for index in range(influences):
            result[offset + index] = row[index] / total if index in keep else 0.0
    return result


def write_artifacts(diagnostic_path: Path, weights_path: Path,
                    diagnostic: dict[str, Any], arrays: dict[str, Any]) -> None:
    import numpy as np

    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8")
    np.savez_compressed(weights_path, **arrays)
