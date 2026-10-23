"""Read-only, bulk mesh snapshots for lossy reduction and exact clean copying."""

from __future__ import annotations

import hashlib
import json


def modules():
    from maya import cmds
    from maya.api import OpenMaya as om
    from maya.api import OpenMayaAnim as oma
    return cmds, om, oma


def resolve(source, expected_uuid):
    cmds, _om, _oma = modules()
    if not isinstance(source, str) or not source.startswith("|") or any(c in source for c in "*?[]"):
        raise ValueError("source must be an exact absolute transform DAG path")
    found = cmds.ls(source, long=True) or []
    if found != [source] or cmds.nodeType(source) != "transform":
        raise ValueError("source must resolve to exactly one mesh transform: " + source)
    if not expected_uuid or (cmds.ls(source, uuid=True) or []) != [expected_uuid]:
        raise ValueError("Source UUID changed: " + source)
    shapes = cmds.listRelatives(source, shapes=True, noIntermediate=True,
                                fullPath=True, type="mesh") or []
    children = cmds.listRelatives(source, children=True, type="transform") or []
    if len(shapes) != 1 or children:
        raise ValueError("Expected a leaf transform with one output mesh: " + source)
    if len(cmds.listRelatives(shapes[0], allParents=True, fullPath=True) or []) != 1:
        raise ValueError("Instanced mesh is protected: " + source)
    return shapes[0]


def mesh_fn(shape):
    _cmds, om, _oma = modules()
    selected = om.MSelectionList()
    selected.add(shape)
    dag = selected.getDagPath(0)
    return dag, om.MFnMesh(dag)


def flat(value):
    if isinstance(value, (list, tuple)):
        return [part for row in value for part in flat(row)]
    return [float(value)]


def visibility_signature(node):
    from cgi_pipeline.capabilities.maya.display_inheritance import display_state
    cmds, _om, _oma = modules()
    shape = (cmds.listRelatives(node, shapes=True, noIntermediate=True,
                                fullPath=True, type="mesh") or [None])[0]
    return {label + "." + attr: {"value": value["value"], "sources": value["inputs"]}
            for label, target in (("transform", node), ("shape", shape)) if target
            for attr, value in display_state(cmds, target).items()}


def copy_visibility(signature, node):
    from cgi_pipeline.capabilities.maya.display_inheritance import apply_state
    cmds, _om, _oma = modules()
    shape = cmds.listRelatives(node, shapes=True, noIntermediate=True,
                               fullPath=True, type="mesh")[0]
    for label, target in (("transform", node), ("shape", shape)):
        state = {key.split(".", 1)[1]: {"value": value["value"], "inputs": value["sources"]}
                 for key, value in signature.items() if key.startswith(label + ".")}
        apply_state(cmds, state, target)


def protect_dependencies(source, shape):
    cmds, _om, _oma = modules()
    for attr in ("translate", "rotate", "scale", "shear", "offsetParentMatrix"):
        if cmds.listConnections(source + "." + attr, source=True, destination=False):
            raise ValueError("Driven mesh transform requires a dedicated strategy: " + source)
    for attr in ("outMesh", "worldMesh"):
        if cmds.listConnections(shape + "." + attr, source=False, destination=True, plugs=True):
            raise ValueError("Mesh output drives another node; protected: " + shape)
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    skins = [n for n in history if cmds.nodeType(n) == "skinCluster"]
    other = [n for n in history if cmds.objectType(n, isAType="geometryFilter")
             and cmds.nodeType(n) not in {"skinCluster", "tweak"}]
    if len(skins) > 1 or other:
        raise ValueError("Non-skin deformation requires protection: " + repr(other or skins))
    return skins[0] if skins else ""


def snapshot(source, skin=""):
    cmds, om, _oma = modules()
    shape = cmds.listRelatives(source, shapes=True, noIntermediate=True,
                               fullPath=True, type="mesh")[0]
    dag, fn = mesh_fn(shape)
    if fn.numPolygons < 1 or fn.numVertices < 1:
        raise ValueError("Mesh must contain polygon faces and vertices: " + source)
    counts, indices = fn.getVertices()
    shaders, assignments = fn.getConnectedShaders(dag.instanceNumber())
    identities = [om.MFnDependencyNode(obj).uuid().asString() for obj in shaders]
    face_materials = [identities[i] if i >= 0 else None for i in assignments]
    if len(face_materials) != fn.numPolygons or None in face_materials:
        raise ValueError("All output faces must have a valid material assignment: " + source)
    uvs, partitions = {}, {}
    for name in sorted(fn.getUVSetNames()):
        us, vs = fn.getUVs(name)
        uv_counts, uv_ids = fn.getAssignedUVs(name)
        if len(uv_counts) != len(counts) or len(us) != len(vs):
            raise ValueError("Invalid UV data: " + source + ":" + name)
        corners, labels, seen, offset = [], [], {}, 0
        for face_size, assigned in zip(counts, uv_counts):
            if assigned not in (0, face_size):
                raise ValueError("Partial UV face assignments are unsupported")
            ids = uv_ids[offset:offset + assigned]
            corners.extend([(float(us[i]), float(vs[i])) for i in ids] if assigned else [None] * face_size)
            labels.extend([seen.setdefault(int(i), len(seen)) for i in ids] if assigned else [None] * face_size)
            offset += assigned
        uvs[name] = corners
        partitions[name] = labels
    result = {"source": source, "uuid": cmds.ls(source, uuid=True)[0], "shape": shape,
              "counts": {"faces": fn.numPolygons, "vertices": fn.numVertices},
              "points": [tuple(point)[:3] for point in fn.getPoints(om.MSpace.kWorld)],
              "face_counts": list(counts), "face_indices": list(indices),
              "uvs": uvs, "uv_partition": partitions, "materials": face_materials,
              "history": [(n, cmds.nodeType(n)) for n in (cmds.listHistory(shape, pruneDagObjects=True) or [])],
              "skin_cluster": skin, "visibility": visibility_signature(source)}
    if skin:
        from .skin import capture_weights
        result.update(capture_weights(skin, shape))
    return result


def fingerprint(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _near(left, right, tolerance):
    if left is None or right is None:
        return left is right
    return len(left) == len(right) and all(abs(a - b) <= tolerance for a, b in zip(left, right))


def verify_clean(expected, actual, *, check_skin=False):
    checks = {
        "topology": expected["face_counts"] == actual["face_counts"] and expected["face_indices"] == actual["face_indices"],
        "world_points": len(expected["points"]) == len(actual["points"]) and all(
            _near(a, b, 1e-5) for a, b in zip(expected["points"], actual["points"])),
        "material_assignments": expected["materials"] == actual["materials"],
        "uv_sets": set(expected["uvs"]) == set(actual["uvs"]),
        "construction_history": not any(kind in {"polyReduce", "polyUnite", "polyUniteSkinned"}
                                         for _node, kind in actual["history"]),
    }
    checks["uv_corners"] = checks["uv_sets"] and all(
        len(expected["uvs"][name]) == len(actual["uvs"][name]) and all(
            _near(a, b, 1e-6) for a, b in zip(expected["uvs"][name], actual["uvs"][name]))
        for name in expected["uvs"])
    checks["uv_sharing"] = expected["uv_partition"] == actual["uv_partition"]
    if check_skin:
        from .skin import verify_weights
        checks.update(verify_weights(expected, actual))
    if not all(checks.values()):
        raise RuntimeError("Clean geometry verification failed: " + repr(checks))
    return checks


def concatenate(items):
    result = {key: [] for key in ("points", "face_counts", "face_indices", "materials", "weights", "blend_weights")}
    result["uvs"] = {key: [] for key in items[0]["uvs"]}
    result["uv_partition"] = {key: [] for key in items[0]["uvs"]}
    next_uv_label = dict.fromkeys(result["uvs"], 0)
    for item in items:
        if set(item["uvs"]) != set(result["uvs"]):
            raise ValueError("UV set names must match before combine")
        offset = len(result["points"])
        result["face_indices"].extend(index + offset for index in item["face_indices"])
        for key in ("points", "face_counts", "materials", "weights", "blend_weights"):
            result[key].extend(item.get(key, []))
        for key in result["uvs"]:
            result["uvs"][key].extend(item["uvs"][key])
            labels = result["uv_partition"][key]
            start = next_uv_label[key]
            for label in item["uv_partition"][key]:
                shifted = None if label is None else label + start
                labels.append(shifted)
                if shifted is not None:
                    next_uv_label[key] = max(next_uv_label[key], shifted + 1)
    return result


def summary(item):
    return {"path": item["source"], "uuid": item["uuid"], "counts": item["counts"],
            "skin_cluster": item["skin_cluster"], "uv_sets": sorted(item["uvs"]),
            "material_uuids": sorted(set(item["materials"])),
            "history_types": sorted({kind for _node, kind in item["history"]})}
