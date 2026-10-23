"""Read-only structural inventory and bounded class representatives for Proxy planning."""
from __future__ import annotations

import hashlib
import json
import re

from . import deletion_scope, membership, outside, target_scope

POLICY_VERSION = "proxy-rig-1.1"
from cgi_pipeline.capabilities.maya.display_inheritance import DISPLAY_ATTRS, display_state, visibility_context


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def identity(cmds, node):
    values = cmds.ls(node, uuid=True) or []
    if len(values) != 1:
        raise ValueError("Expected one node identity: " + node)
    return str(values[0])


def category(path):
    tokens = re.sub(r"([a-z])([A-Z])", r"\1_\2", path).lower()
    for name, words in (("hair", ("hair",)), ("feather", ("feather",)),
                        ("wing", ("wing",)), ("head", ("head", "face")),
                        ("eyes", ("eye",)), ("cloth", ("cloth", "dress")),
                        ("body", ("body",)), ("hiddenMesh", ("hiddenmesh", "hidden_mesh"))):
        if any(word in tokens for word in words):
            return name
    return "other"


def skin_info(cmds, skin):
    return {"name": skin, "uuid": identity(cmds, skin),
            "method": cmds.getAttr(skin + ".skinningMethod"),
            "normalize": cmds.getAttr(skin + ".normalizeWeights"),
            "influences": sorted(cmds.skinCluster(skin, query=True, influence=True) or [])}


def assigned_materials(shape):
    """Material identity means actual face assignments, not stale SG connections."""
    import maya.api.OpenMaya as om
    selection = om.MSelectionList()
    selection.add(shape)
    dag = selection.getDagPath(0)
    mesh = om.MFnMesh(dag)
    shaders, assignments = mesh.getConnectedShaders(dag.instanceNumber())
    used = set(assignments)
    if len(assignments) != mesh.numPolygons or any(i < 0 or i >= len(shaders) for i in used):
        raise ValueError("Output faces lack a material assignment: " + shape)
    return sorted(om.MFnDependencyNode(shaders[i]).name() for i in used)


def row(cmds, shape, skins, sets):
    transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [""])[0]
    history = sorted(set(cmds.listHistory(shape, pruneDagObjects=True) or []))
    deformers = [node for node in history if "geometryFilter" in
                 (cmds.nodeType(node, inherited=True) or [])]
    clusters = [node for node in deformers if cmds.nodeType(node) == "skinCluster"]
    for skin in clusters:
        if skin not in skins:
            skins[skin] = skin_info(cmds, skin)
    context = visibility_context(cmds, transform)
    shape_paths = cmds.ls(shape, long=True, allPaths=True) or []
    shapes = cmds.listRelatives(transform, shapes=True, noIntermediate=True,
                                fullPath=True, type="mesh") or []
    unsupported = []
    if len(shape_paths) != 1 or len(shapes) != 1 or len(cmds.listRelatives(
            shape, allParents=True, fullPath=True) or []) != 1:
        unsupported.append("instanced_or_multiple_output_shapes")
    if len(clusters) > 1:
        unsupported.append("multiple_skin_clusters")
    if any(cmds.nodeType(node) not in ("skinCluster", "tweak") for node in deformers):
        unsupported.append("non_skin_deformer")
    if cmds.referenceQuery(transform, isNodeReferenced=True):
        unsupported.append("referenced_output")
    memberships = membership.inspect(cmds, transform, shape, sets)
    if memberships["component_sets"]:
        unsupported.append("component_object_set_membership")
    deletion = deletion_scope.inspect(cmds, transform, shapes, history)
    if deletion["external_consumers"]:
        unsupported.append("child_shape_external_consumer")
    return {"uuid": identity(cmds, transform), "shape_uuid": identity(cmds, shape),
            "path": transform, "shape": shape, "parent": context["parent"],
            "faces": int(cmds.polyEvaluate(shape, face=True)),
            "vertices": int(cmds.polyEvaluate(shape, vertex=True)),
            "bbox": [round(float(x), 7) for x in cmds.exactWorldBoundingBox(shape)],
            "skin": skins[clusters[0]] if len(clusters) == 1 else None,
            "blendshapes": [node for node in deformers if cmds.nodeType(node) == "blendShape"],
            "deformers": [{"name": node, "type": cmds.nodeType(node)} for node in deformers],
            "display": context, "shape_display": display_state(cmds, shape),
            "visibility_signature": digest({"transform": context, "shape": display_state(cmds, shape)}),
            "category_candidate": category(transform), "protected_reasons": unsupported,
            "memberships": memberships,
            "deletion_shapes": deletion["shapes"], "child_shape_consumers": deletion["external_consumers"],
            "uv_sets": sorted(cmds.polyUVSet(shape, query=True, allUVSets=True) or []),
            "materials": assigned_materials(shape)}


def representatives(rows):
    groups = {}
    for item in rows:
        groups.setdefault(item["category_candidate"], []).append(item)
    summary = {}
    for name, items in sorted(groups.items()):
        ordered = sorted(items, key=lambda item: (item["faces"], item["uuid"]))
        indices = sorted({0, len(items) // 2, len(items) - 1})
        median = ordered[len(items) // 2]["faces"]
        outliers = [item for item in ordered if item["faces"] > max(median * 4, 10000)]
        summary[name] = {"mesh_count": len(items), "faces": sum(x["faces"] for x in items),
                         "representatives": [{key: ordered[i][key] for key in
                          ("uuid", "path", "faces", "bbox", "protected_reasons")} for i in indices],
                         "outlier_count": len(outliers),
                         "largest_outliers": [{key: item[key] for key in ("uuid", "path", "faces")}
                                              for item in reversed(outliers[-5:])]}
    return summary


def inspect(cmds, project="", cache_group="", *, exclude_transforms=()):
    cache_root, output_shapes, candidates = target_scope.resolve(cmds, project, cache_group)
    skins, rows, sets = {}, [], {}
    excluded = frozenset(exclude_transforms)
    for shape in output_shapes:
        if shape.rsplit('|', 1)[0] in excluded:
            continue
        rows.append(row(cmds, shape, skins, sets))
    external = outside.inspect(cmds, cache_root)
    scene = (cmds.file(query=True, sceneName=True) or "").replace("\\", "/")
    body = {"policy_version": POLICY_VERSION, "scene_path": scene,
            "project": project, "cache_root": cache_root,
            "frame": float(cmds.currentTime(query=True)), "meshes": rows, "outside_cache": external}
    external_candidates = [item for item in external if item["status"] == "candidate"]
    external_protected = [item for item in external if item["status"] == "protected"]
    return {**body, "cache_candidates": candidates, "scene_fingerprint": digest(body), "categories": representatives(rows),
            "mesh_count": len(rows), "faces": sum(item["faces"] for item in rows),
            "outside_cache_summary": {"total": len(external), "candidate_count": len(external_candidates),
               "protected_count": len(external_protected), "candidate_examples": external_candidates[:10],
               "protected_examples": external_protected[:5], "optimization": "outside-cache models are not optimization targets"},
            "fingerprint_scope": "Structural identity, pose bounds, display and binding metadata; not a full weight-content hash.",
            "policy": {"classify": "names/hierarchy then mechanism/display compatibility then representative visual review",
                       "target_scope": "configured rig cache outputs via shared asset_info_collector",
                       "hidden_is_unused": False, "unknown_bindings": "protect and report",
                       "reduction": "AI chooses class parameters after representative observation; no global fixed reduction"}}
