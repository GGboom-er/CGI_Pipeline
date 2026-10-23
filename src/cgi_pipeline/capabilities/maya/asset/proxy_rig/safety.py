"""Proxy-specific deletion, output and result boundaries; no global scene cleanup."""
from __future__ import annotations

import json
from pathlib import Path
import re
import uuid

from cgi_pipeline.core.path_guard import is_protected_path
from cgi_pipeline.core.scene_files import is_managed_path
from cgi_pipeline.paths import REPOSITORY_ROOT

from .inventory import DISPLAY_ATTRS, display_state, identity, visibility_context, assigned_materials
from . import deletion_scope, membership, outside


def managed(path, *, suffix=None):
    value = Path(path).resolve()
    if is_protected_path(str(value)) or not is_managed_path(value, REPOSITORY_ROOT):
        raise ValueError("Proxy artifacts must stay inside the managed projects/runs sandbox")
    if suffix and value.suffix.lower() not in suffix:
        raise ValueError("Unsupported Proxy artifact extension")
    return value


def artifact_root(payload, scene):
    base = payload.get("run_dir") or str(Path(scene).parent)
    return managed(Path(base) / ".info" / "proxy_rig")


def write_report(root, label, data):
    root.mkdir(parents=True, exist_ok=True)
    target = root / (label + "_" + uuid.uuid4().hex[:12] + ".json")
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target).replace("\\", "/")


def output_path(value, character, source):
    if not value:
        raise ValueError("An explicit sandbox Proxy output_path is required")
    target = managed(value, suffix={".ma", ".mb"})
    if character.lower() not in target.stem.lower() or not re.search(
            r"_proxyRig_v\d+$", target.stem, re.IGNORECASE):
        raise ValueError("output_path must name the character and an independent _proxyRig_vNNN version")
    if target == Path(source).resolve():
        raise ValueError("Proxy output cannot overwrite its source scene")
    return target


def source_consumers(cmds, row):
    """Refuse live geometric/name consumers instead of guessing their replacement."""
    allowed = {node["name"] for node in row["deformers"]}
    allowed_types = {"shadingEngine", "objectSet", "groupId", "groupParts", "displayLayer"}
    problems = []
    for source in (row["path"], row["shape"]):
        for consumer in sorted(set(cmds.listConnections(
                source, source=False, destination=True) or [])):
            if consumer not in allowed and cmds.nodeType(consumer) not in allowed_types:
                problems.append(consumer)
    deletion = deletion_scope.inspect(cmds, row["path"], [row["shape"]])
    return problems + deletion["external_consumers"]


def check_consumers(cmds, rows):
    scripts = outside.script_sources(cmds)
    for row in rows:
        problems = source_consumers(cmds, row)
        names = [row["path"], row["shape"]] + [item["path"] for item in row.get("deletion_shapes", [])]
        problems.extend(node + ":name_reference" for node in outside.name_users(names, scripts))
        if problems:
            raise ValueError(row["path"] + ": active downstream consumers require protection: " +
                             ", ".join(sorted(set(problems))))
        children = cmds.listRelatives(row["path"], children=True, fullPath=True,
                                       type="transform") or []
        if children:
            raise ValueError(row["path"] + ": source transform has child transforms; protect it")


def preflight_names(cmds, normalized):
    replaced = {normalized["by_id"][source]["path"] for source in normalized["changed"]}
    names = [entry["output_name"] for entry in normalized["reductions"] + normalized["merges"]]
    names += sorted({r['output_name'] for e in normalized.get('region_entries', []) for r in e['regions']})
    for name in names:
        conflicts = cmds.ls(name, long=True) or []
        if any(path not in replaced for path in conflicts):
            raise ValueError("Planned output name is already used outside the replacement scope: " + name)


def resolve(cmds, node_uuid):
    values = cmds.ls(node_uuid, long=True) or []
    if len(values) != 1:
        raise RuntimeError("Expected one surviving result UUID: " + node_uuid)
    return values[0]


def check_result(cmds, result, sources, *, merged=False, mapped=None):
    shapes = cmds.listRelatives(result, shapes=True, noIntermediate=True,
                                fullPath=True, type="mesh") or []
    if len(shapes) != 1:
        raise RuntimeError("Expected one final polygon output: " + result)
    shape = shapes[0]
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    forbidden = [node for node in history if cmds.nodeType(node) in
                 ("polyReduce", "polyUnite", "polyUniteSkinned")]
    if forbidden:
        raise RuntimeError("Final output retains construction history: " + str(forbidden))
    expected_uvs = set().union(*(set(row["uv_sets"]) for row in sources))
    expected_materials = set().union(*(set(row["materials"]) for row in sources))
    uvs = set(cmds.polyUVSet(shape, query=True, allUVSets=True) or [])
    materials = set(assigned_materials(shape))
    if uvs != expected_uvs or not materials or not materials.issubset(expected_materials):
        raise RuntimeError("Final UV-set or assigned material identities differ for {}: UVs={}, expected={}, materials={}, allowed={}".format(
            result, sorted(uvs), sorted(expected_uvs), sorted(materials), sorted(expected_materials)))
    skins = cmds.ls(history, type="skinCluster") or []
    if any(row["skin"] for row in sources) and len(skins) != 1:
        raise RuntimeError("Expected one inherited final skinCluster")
    if visibility_context(cmds, result) != sources[0]["display"]:
        raise RuntimeError("Final output did not preserve its display/visibility switching context")
    if display_state(cmds, shape) != sources[0]["shape_display"]:
        raise RuntimeError("Final shape display switching context differs from its source")
    memberships = membership.inspect(cmds, result, shape, {})
    expected_memberships = dict(sources[0].get('memberships', memberships))
    if mapped:
        expected_memberships['component_sets'] = memberships['component_sets']
    if memberships != expected_memberships:
        raise RuntimeError("Final output changed user object-set or display-layer membership")
    faces = int(cmds.polyEvaluate(shape, face=True))
    if faces < 1 or faces > sum(row["faces"] for row in sources):
        raise RuntimeError("Final face count is empty or larger than the source scope")
    return {"result": result, "result_uuid": identity(cmds, result), "faces": faces,
            "vertices": int(cmds.polyEvaluate(shape, vertex=True)),
            "skin_cluster": skins[0] if skins else "", "uv_sets": sorted(uvs),
            "materials": sorted(materials), "construction_history_clean": True,
            "display_preserved": True, "merged": merged,
            "lossy_geometry": "Numerical topology/UV equality is intentionally not required after reduction"}


from cgi_pipeline.capabilities.maya.display_inheritance import copy_display
