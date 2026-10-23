"""Bounded evaluated polygon candidate sets and camera-clipped nearest intersections."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from itertools import islice

from .scene_observation_io import canonical

_DRAW_FLAGS = {"nurbsCurve": "nurbsCurves", "nurbsSurface": "nurbsSurfaces",
               "subdiv": "subdivSurfaces", "stroke": "strokes", "hairSystem": "hairSystems",
               "fluidShape": "fluids", "particle": "dynamics", "nParticle": "nParticles",
               "imagePlane": "imagePlane"}
_AUXILIARY = {"transform", "joint", "camera", "locator", "pointLight", "directionalLight",
              "spotLight", "areaLight", "ambientLight", "volumeLight", "ikHandle", "ikEffector"}
_NATIVE_CONSTRAINTS = {"parentConstraint", "pointConstraint", "orientConstraint", "scaleConstraint",
                       "aimConstraint", "geometryConstraint", "normalConstraint", "tangentConstraint",
                       "poleVectorConstraint"}
_DRAW_DEFAULTS = {"visibility": True, "lodVisibility": True, "template": False, "ghosting": False,
                  "hideOnPlayback": False, "overrideEnabled": False, "overrideDisplayType": 0,
                  "overrideLevelOfDetail": 0, "overrideShading": True, "overrideTexturing": True,
                  "overridePlayback": True, "overrideVisibility": True, "overrideRGBColors": False,
                  "overrideColor": 0, "overrideColorRGB": [(0.0, 0.0, 0.0)], "overrideColorA": 1.0}
_BASE_SHADERS = {"lambert", "phong", "phongE", "blinn", "surfaceShader"}
_OPAQUE_VALUES = {"transparency": 0.0, "outTransparency": 0.0, "opacity": 1.0,
                  "refractions": 0.0, "transmission": 0.0, "transmissionWeight": 0.0, "refraction": 0.0}
_APPEARANCE_ATTRS = tuple(_OPAQUE_VALUES) + ("color", "outColor", "ambientColor", "incandescence",
    "diffuse", "specularColor", "cosinePower", "eccentricity", "specularRollOff", "refractiveIndex")


def dag_path(path):
    import maya.api.OpenMaya as om
    selection = om.MSelectionList()
    selection.add(path)
    return selection.getDagPath(0)


def _native_constraint_auxiliary(node_type, dag):
    if node_type not in _NATIVE_CONSTRAINTS:
        return False
    import maya.api.OpenMaya as om
    # MPxConstraint plugins have their own function-set type; neither arbitrary
    # transform inheritance nor a name containing "Constraint" establishes safety.
    return bool(dag.node().hasFn(om.MFn.kConstraint))


def _native_annotation_auxiliary(node_type, dag):
    if node_type != "annotationShape":
        return False
    import maya.api.OpenMaya as om
    return bool(dag.node().hasFn(om.MFn.kAnnotation))


def _attribute(cmds, path, attribute, default=None):
    if cmds.attributeQuery(attribute, node=path, exists=True):
        return cmds.getAttr(path + "." + attribute)
    return default


def _check_drawing(node, values):
    if values["template"] or values["ghosting"] or values["hideOnPlayback"]:
        raise ValueError("Template, ghosting or hideOnPlayback drawing is unsupported: " + node)
    if not values["lodVisibility"]:
        raise ValueError("LOD visibility drawing is unsupported: " + node)
    if not values["overrideEnabled"]:
        return
    if (values["overrideDisplayType"] not in (0, 2) or values["overrideLevelOfDetail"] != 0
            or not values["overrideShading"] or not values["overrideTexturing"]):
        raise ValueError("Only normal/reference full shaded drawing overrides are supported: " + node)
    if not values["overrideVisibility"] or float(values["overrideColorA"]) != 1.0:
        raise ValueError("Hidden or alpha drawing override is unsupported: " + node)


def _drawing(cmds, path, cache):
    parents = ["|" + "|".join(path.split("|")[1:index])
               for index in range(2, len(path.split("|")) + 1)]
    records, references = [], []
    for node in parents:
        if node not in cache:
            values = {key: _attribute(cmds, node, key, default) for key, default in _DRAW_DEFAULTS.items()}
            _check_drawing(node, values)
            values = json.loads(canonical(values))
            layers = sorted(set(cmds.listConnections(node, type="displayLayer") or []))
            if any(layer != "defaultLayer" for layer in layers):
                raise ValueError("Non-default display layer is unsupported: " + node)
            cache[node] = {"path": node, "attributes": values, "display_layers": layers}
        record = cache[node]
        records.append(record)
        values = record["attributes"]
        if values["overrideEnabled"] and values["overrideDisplayType"] == 2:
            references.append(node)
    return {"nodes": records, "reference_display_nodes": references,
            "ui_selection_and_editability_not_certified": True}


def _hash_numbers(values, format_code):
    digest = hashlib.sha256()
    iterator = iter(values)
    while True:
        chunk = []
        try:
            for value in islice(iterator, 8192):
                if not math.isfinite(float(value)):
                    raise ValueError("Nonfinite evaluated geometry is unsupported")
                chunk.append(value)
            if not chunk:
                break
            packed = struct.pack("<" + str(len(chunk)) + format_code, *chunk)
        except Exception:
            # Preserve an earlier scalar packing error before a later validation/iterator error.
            for value in chunk:
                struct.pack("<" + format_code, value)
            raise
        digest.update(packed)
    return digest.hexdigest()


def _points(fn):
    import maya.api.OpenMaya as om
    points = fn.getPoints(om.MSpace.kWorld)
    if not points:
        raise ValueError("Meshes without vertices cannot be grounded")
    bounds = [min(getattr(p, axis) for p in points) for axis in ("x", "y", "z")]
    bounds += [max(getattr(p, axis) for p in points) for axis in ("x", "y", "z")]
    digest = _hash_numbers((value for p in points for value in (p.x, p.y, p.z)), "d")
    return bounds, digest


def _topology(fn, max_vertices):
    if fn.numPolygons > max_vertices * 4:
        raise ValueError("Mesh polygon count exceeds the topology budget")
    counts, indices = fn.getVertices()
    triangles, triangle_indices = fn.getTriangles()
    if len(indices) + len(triangle_indices) > max_vertices * 48:
        raise ValueError("Mesh connectivity exceeds the topology budget")
    return {"polygon_count": int(fn.numPolygons),
            "polygon_counts_sha256": _hash_numbers(counts, "q"),
            "polygon_vertices_sha256": _hash_numbers(indices, "q"),
            "triangle_counts_sha256": _hash_numbers(triangles, "q"),
            "triangle_vertices_sha256": _hash_numbers(triangle_indices, "q")}


def _opaque_attributes(cmds, shader):
    records, reasons = {}, []
    for attribute in _APPEARANCE_ATTRS:
        present = bool(cmds.attributeQuery(attribute, node=shader, exists=True))
        value = cmds.getAttr(shader + "." + attribute) if present else None
        sources = sorted(set(cmds.listConnections(shader + "." + attribute, source=True,
                        destination=False, plugs=True) or [])) if present else []
        records[attribute] = {"present": present, "value": value, "source_plugs": sources}
        if present and attribute in _OPAQUE_VALUES:
            values = value if isinstance(value, (list, tuple)) else [value]
            if values and isinstance(values[0], (list, tuple)):
                values = values[0]
            if sources or any(not math.isfinite(float(v)) or float(v) != _OPAQUE_VALUES[attribute] for v in values):
                reasons.append("Unsupported opacity/refraction input: " + shader + "." + attribute)
    return {"attributes": json.loads(canonical(records)), "unsupported_reasons": reasons}


def _shader_appearance(cmds, shader):
    record = _opaque_attributes(cmds, shader)
    shader_type = cmds.nodeType(shader)
    if shader_type not in _BASE_SHADERS:
        record["unsupported_reasons"].append("Unsupported shader type: " + shader_type + " at " + shader)
    required = "outTransparency" if shader_type == "surfaceShader" else "transparency"
    if not record["attributes"][required]["present"]:
        record["unsupported_reasons"].append("Missing native opacity attribute: " + shader + "." + required)
    record.update(name=shader, uuid=(cmds.ls(shader, uuid=True) or [None])[0], type=shader_type,
                  pixel_lookup_supported=not record["unsupported_reasons"])
    return record


def _shading_group(cmds, name, shader_cache):
    shaders = cmds.listConnections(name + ".surfaceShader", source=True, destination=False) or []
    entries, reasons = [], []
    for shader in sorted(set(shaders)):
        if shader not in shader_cache:
            shader_cache[shader] = _shader_appearance(cmds, shader)
        entries.append(shader_cache[shader])
    connections = {key: sorted(set(cmds.listConnections(name + "." + key, source=True,
                        destination=False, plugs=True) or []))
                   for key in ("surfaceShader", "displacementShader", "volumeShader")}
    if len(entries) != 1:
        reasons.append("Exactly one known surface shader required: " + name)
    if connections["displacementShader"] or connections["volumeShader"]:
        reasons.append("Displacement or volume shader is unsupported: " + name)
    reasons.extend(reason for entry in entries for reason in entry["unsupported_reasons"])
    return {"shading_group": name, "shading_group_uuid": (cmds.ls(name, uuid=True) or [None])[0],
            "connections": connections, "shaders": entries}, reasons


def _materials(cmds, fn, instance, cache=None):
    import maya.api.OpenMaya as om
    groups, assignments = fn.getConnectedShaders(instance)
    cache = {"groups": {}, "shaders": {}} if cache is None else cache
    result, reasons = [], []
    for group in groups:
        name = om.MFnDependencyNode(group).name()
        if name not in cache["groups"]:
            cache["groups"][name] = _shading_group(cmds, name, cache["shaders"])
        record, group_reasons = cache["groups"][name]
        result.append(record)
        reasons.extend(group_reasons)
    if not groups or any(index < 0 or index >= len(groups) for index in assignments):
        reasons.append("Missing or unsupported polygon shader assignment")
    return {"groups": result, "face_assignments_sha256": _hash_numbers(assignments, "q"),
            "pixel_lookup_supported": not reasons, "unsupported_reasons": sorted(set(reasons))}


def mesh_record(cmds, path, dag, identity, remaining_vertices, maximum, material_cache=None):
    import maya.api.OpenMaya as om
    fn = om.MFnMesh(dag)
    count = int(fn.numVertices)
    if count > remaining_vertices:
        raise ValueError("Complete visible mesh set exceeds max_vertices; no truncated grounding")
    if _attribute(cmds, path, "displaySmoothMesh", 0):
        raise ValueError("Smooth preview differs from base polygon intersections: " + path)
    if (not _attribute(cmds, path, "doubleSided", True)
            or _attribute(cmds, path, "backfaceCulling", 0)
            or _attribute(cmds, path, "invisibleFaces", [])):
        raise ValueError("Per-mesh backface or invisible-face drawing is unsupported: " + path)
    bounds, points_hash = _points(fn)
    result = identity(cmds, path)
    result.update(transform=identity(cmds, path.rsplit("|", 1)[0]),
                  world_matrix_row_major=list(dag.inclusiveMatrix()), vertex_count=count,
                  points_world_sha256=points_hash, topology=_topology(fn, maximum),
                  world_bounds=bounds,
                  world_center=[(bounds[i] + bounds[i + 3]) / 2 for i in range(3)],
                  shading_assignment=_materials(cmds, fn, dag.instanceNumber(), material_cache))
    result["appearance"] = {key: result["shading_assignment"][key]
                            for key in ("pixel_lookup_supported", "unsupported_reasons")}
    return result


def _candidate_paths(cmds, display):
    candidates, auxiliary = [], set()
    paths = sorted(set(cmds.ls(dag=True, long=True, allPaths=True) or []))
    if len(paths) > 100000:
        raise ValueError("DAG enumeration exceeds the grounding budget")
    for path in paths:
        node_type = cmds.nodeType(path)
        if node_type in _AUXILIARY:
            auxiliary.add(node_type)
            continue
        dag = dag_path(path)
        if not dag.isVisible() or _attribute(cmds, path, "intermediateObject", False):
            continue
        if node_type != "mesh":
            if (_native_constraint_auxiliary(node_type, dag)
                    or _native_annotation_auxiliary(node_type, dag)):
                auxiliary.add(node_type)
                continue
            if _DRAW_FLAGS.get(node_type) and not display[_DRAW_FLAGS[node_type]]:
                continue
            raise ValueError("Unsupported visible drawable type: " + node_type + " at " + path)
        candidates.append((path, dag))
    return candidates, auxiliary


def collect(cmds, display, identity, max_meshes, max_vertices):
    records, used, drawing_cache = [], 0, {}
    material_cache = {"groups": {}, "shaders": {}}  # Scoped to this snapshot, never a later validation.
    candidates, auxiliary = _candidate_paths(cmds, display)
    if len(candidates) > max_meshes:
        raise ValueError("Complete visible mesh set exceeds max_meshes; no truncated grounding")
    for path, dag in candidates:
        drawing = _drawing(cmds, path, drawing_cache)
        if any(not n["attributes"]["visibility"] for n in drawing["nodes"]):
            raise ValueError("Native path visibility conflicts with ancestor visibility: " + path)
        record = mesh_record(cmds, path, dag, identity, max_vertices - used, max_vertices, material_cache)
        record["drawing"] = drawing
        used += record["vertex_count"]
        records.append(record)
    if not records:
        raise ValueError("No supported visible polygon geometry exists in this panel")
    return records, {"mesh_instances": len(records), "vertices": used,
                     "unsupported_mesh_count": sum(m.get("appearance", {}).get("pixel_lookup_supported")
                                                    is not True for m in records),
                     "auxiliary_types_excluded_from_surface_picking": sorted(auxiliary)}


def target_records(cmds, targets, meshes, identity, view, project):
    if not isinstance(targets, (list, tuple)) or not 1 <= len(targets) <= 200:
        raise ValueError("Grounding requires 1..200 explicit full DAG targets")
    if len(set(targets)) != len(targets):
        raise ValueError("Duplicate grounding targets are not allowed")
    output = []
    for path in targets:
        if not isinstance(path, str) or not path.startswith("|"):
            raise ValueError("Grounding targets require exact full DAG paths")
        record = identity(cmds, path)
        if record["node_type"] not in ("transform", "mesh"):
            raise ValueError("Grounding targets support transform or mesh paths only")
        children = [m for m in meshes if m["path"] == path or m["path"].startswith(path + "|")]
        if not children:
            raise ValueError("Target has no supported visible polygon geometry: " + path)
        bounds = [min(m["world_bounds"][i] for m in children) for i in range(3)]
        bounds += [max(m["world_bounds"][i + 3] for m in children) for i in range(3)]
        center = [(bounds[i] + bounds[i + 3]) / 2 for i in range(3)]
        record.update(mesh_paths=[m["path"] for m in children], world_bounds=bounds,
                      world_center=center, projection=project(view, center),
                      reference_display_nodes=sorted({p for m in children
                          for p in m.get("drawing", {}).get("reference_display_nodes", [])}),
                      pixel_lookup_supported=all(m.get("appearance", {}).get("pixel_lookup_supported")
                                                 is True for m in children),
                      pixel_lookup_unsupported_reasons=sorted({m["path"] + ": " + reason for m in children
                          for reason in m.get("appearance", {}).get("unsupported_reasons", ["Appearance not recorded"])}),
                      ui_selection_and_editability_not_certified=True)
        output.append(record)
    return output


def _hit_record(mesh, hit):
    point, distance, face, triangle, bary1, bary2 = hit
    numbers = [float(v) for v in (point.x, point.y, point.z, distance, bary1, bary2)]
    if not all(math.isfinite(v) for v in numbers):
        raise RuntimeError("Nonfinite native intersection cannot establish geometry identity")
    return {"path": mesh["path"], "uuid": mesh["uuid"], "instance_number": mesh["instance_number"],
            "transform_path": mesh["transform"]["path"], "transform_uuid": mesh["transform"]["uuid"],
            "pixel_lookup_supported": mesh.get("appearance", {}).get("pixel_lookup_supported") is True,
            "pixel_lookup_unsupported_reasons": mesh.get("appearance", {}).get("unsupported_reasons", ["Appearance not recorded"]),
            "reference_display_nodes": mesh.get("drawing", {}).get("reference_display_nodes", []),
            "ui_selection_and_editability_not_certified": True,
            "world_hit": numbers[:3], "distance_from_clipped_ray_origin": numbers[3],
            "face": int(face), "triangle": int(triangle),
            "barycentric": [numbers[4], numbers[5], 1 - numbers[4] - numbers[5]]}


def _depth_group(hits, origin):
    if not hits:
        return None
    ordered = sorted(hits, key=lambda h: (h["distance_from_clipped_ray_origin"], h["path"],
                                         h["uuid"], h["instance_number"]))
    minimum = ordered[0]["distance_from_clipped_ray_origin"]
    scale = max(1.0, abs(minimum), *(abs(v) for v in ordered[0]["world_hit"]),
                abs(origin.x), abs(origin.y), abs(origin.z))
    tolerance = max(1e-5, 8 * (2.0 ** -23) * scale)
    policy = {"absolute_floor_world_units": 1e-5, "float32_epsilon": 2.0 ** -23,
              "epsilon_multiplier": 8, "scale_world_units": scale, "effective_world_units": tolerance,
              "units": "Maya API internal world distance units",
              "formula": "max(1e-5, 8*2^-23*max(1,abs(min_depth),abs(ray_origin_xyz),abs(nearest_world_hit_xyz)))",
              "purpose": "conservative_ambiguity_rejection_only"}
    candidates = [h for h in ordered if h["distance_from_clipped_ray_origin"] - minimum <= tolerance]
    if len(candidates) == 1:
        return candidates[0] | {"depth_tolerance": policy}
    return {"ambiguous": True, "geometry_candidates": candidates, "candidate_count": len(candidates),
            "depth_tolerance": policy}


def nearest_hit(meshes, origin, direction, maximum, function_sets=None):
    import maya.api.OpenMaya as om
    values = [origin.x, origin.y, origin.z, direction.x, direction.y, direction.z, maximum]
    if not all(math.isfinite(v) and abs(v) < 3.4e38 for v in values):
        raise ValueError("Intersection inputs must be finite and representable by Maya's float ray API")
    ray_origin = om.MFloatPoint(origin.x, origin.y, origin.z)
    ray_direction = om.MFloatVector(direction.x, direction.y, direction.z)
    function_sets = {} if function_sets is None else function_sets
    hits = []
    for mesh in meshes:
        path = mesh["path"]  # Full instance path, not the shared mesh UUID.
        if path not in function_sets:
            function_sets[path] = om.MFnMesh(dag_path(path))
        fn = function_sets[path]
        hit = fn.closestIntersection(ray_origin, ray_direction, om.MSpace.kWorld, maximum, False)
        if hit is None:
            continue
        record = _hit_record(mesh, hit)
        if record["distance_from_clipped_ray_origin"] < 0 or record["distance_from_clipped_ray_origin"] > maximum:
            continue
        hits.append(record)
    return _depth_group(hits, origin)
