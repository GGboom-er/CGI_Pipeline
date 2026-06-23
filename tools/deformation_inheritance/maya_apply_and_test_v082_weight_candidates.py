import json
from pathlib import Path

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2
import maya.cmds as cmds
import numpy as np


INFO_DIR = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\.info\a_weight_transfer_v082_reboot"
)
WEIGHTS_PATH = INFO_DIR / "v082_weight_candidates_distance_vs_energy.npz"
HYBRID_WEIGHTS_PATH = INFO_DIR / "v082_weight_candidates_hybrid_motion_gate.npz"
V083_WEIGHTS_PATH = INFO_DIR / "v083_weight_candidates_inpaint.npz"
INFLUENCE_JSON_PATH = INFO_DIR / "v082_weight_candidates_influences.json"
SOURCE_DATA = INFO_DIR / "v082_clean_scene_data.npz"
VALIDATION_PATHS = {
    "distance_k64": INFO_DIR / "param_sweep" / "v082p00_distance_k64_correspondence_validation.npz",
    "energy_p03": INFO_DIR / "param_sweep" / "v082p03_energy_n100_correspondence_validation.npz",
}
REPORT_PATH = INFO_DIR / "v082_maya_weight_candidate_test_report.json"
ERROR_ARRAYS_PATH = INFO_DIR / "v082_distance_energy_motion_error_arrays.npz"


CANDIDATES = [
    {
        "key": "distance_k64",
        "mesh": "A_V082TEST_001_distanceK64",
        "group": "A_V082TEST_001_distanceK64_GRP",
        "skin": "A_V082TEST_001_distanceK64_skinCluster",
        "offset_x": -24.0,
        "material": "A_V082TEST_distanceK64_MAT",
        "color": (0.08, 0.26, 1.0),
        "error_set": "CDFDIAG_V082TEST_DISTANCE_JAW25_ERROR_TOP_SET",
    },
    {
        "key": "energy_p03",
        "mesh": "A_V082TEST_002_energyP03",
        "group": "A_V082TEST_002_energyP03_GRP",
        "skin": "A_V082TEST_002_energyP03_skinCluster",
        "offset_x": 24.0,
        "material": "A_V082TEST_energyP03_MAT",
        "color": (1.0, 0.34, 0.04),
        "error_set": "CDFDIAG_V082TEST_ENERGYP03_JAW25_ERROR_TOP_SET",
    },
    {
        "key": "hybrid_m0010_cc3",
        "weights_source": "hybrid",
        "primary_validation": "distance_k64",
        "mesh": "A_V082TEST_003_hybridM0010",
        "group": "A_V082TEST_003_hybridM0010_GRP",
        "skin": "A_V082TEST_003_hybridM0010_skinCluster",
        "offset_x": 48.0,
        "material": "A_V082TEST_hybridM0010_MAT",
        "color": (0.05, 0.8, 0.25),
        "error_set": "CDFDIAG_V082TEST_HYBRIDM0010_JAW25_ERROR_TOP_SET",
    },
    {
        "key": "hybrid_m0050_cc3",
        "weights_source": "hybrid",
        "primary_validation": "distance_k64",
        "mesh": "A_V082TEST_004_hybridM0050",
        "group": "A_V082TEST_004_hybridM0050_GRP",
        "skin": "A_V082TEST_004_hybridM0050_skinCluster",
        "offset_x": 72.0,
        "material": "A_V082TEST_hybridM0050_MAT",
        "color": (0.62, 0.12, 0.9),
        "error_set": "CDFDIAG_V082TEST_HYBRIDM0050_JAW25_ERROR_TOP_SET",
    },
    {
        "key": "v083_inpaint_a025",
        "weights_source": "v083",
        "primary_validation": "distance_k64",
        "mesh": "A_V083TEST_001_inpaintA025",
        "group": "A_V083TEST_001_inpaintA025_GRP",
        "skin": "A_V083TEST_001_inpaintA025_skinCluster",
        "offset_x": 96.0,
        "material": "A_V083TEST_inpaintA025_MAT",
        "color": (0.95, 0.85, 0.05),
        "error_set": "CDFDIAG_V083TEST_INPAINTA025_JAW25_ERROR_TOP_SET",
    },
    {
        "key": "v083_inpaint_a050",
        "weights_source": "v083",
        "primary_validation": "distance_k64",
        "mesh": "A_V083TEST_002_inpaintA050",
        "group": "A_V083TEST_002_inpaintA050_GRP",
        "skin": "A_V083TEST_002_inpaintA050_skinCluster",
        "offset_x": 120.0,
        "material": "A_V083TEST_inpaintA050_MAT",
        "color": (0.05, 0.85, 0.9),
        "error_set": "CDFDIAG_V083TEST_INPAINTA050_JAW25_ERROR_TOP_SET",
    },
    {
        "key": "v083_inpaint_a100",
        "weights_source": "v083",
        "primary_validation": "distance_k64",
        "mesh": "A_V083TEST_003_inpaintA100",
        "group": "A_V083TEST_003_inpaintA100_GRP",
        "skin": "A_V083TEST_003_inpaintA100_skinCluster",
        "offset_x": 144.0,
        "material": "A_V083TEST_inpaintA100_MAT",
        "color": (0.95, 0.05, 0.55),
        "error_set": "CDFDIAG_V083TEST_INPAINTA100_JAW25_ERROR_TOP_SET",
    },
]


def _long(name):
    hits = cmds.ls(name, long=True) or []
    return hits[0] if hits else name


def _leaf(path):
    return str(path).split("|")[-1].split(":")[-1]


def _shape(mesh):
    shapes = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True) or []
    if not shapes:
        shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True) or []
    if not shapes:
        raise RuntimeError("No mesh shape found under %s" % mesh)
    return shapes[0]


def _dag_path(node):
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def _get_points(mesh):
    fn = om2.MFnMesh(_dag_path(_shape(mesh)))
    pts = fn.getPoints(om2.MSpace.kWorld)
    return np.asarray([[p.x, p.y, p.z] for p in pts], dtype=np.float64)


def _set_jaw(value):
    if cmds.objExists("M_Jaw_A_ctrl.rotateX"):
        cmds.setAttr("M_Jaw_A_ctrl.rotateX", float(value))
    cmds.dgdirty(allPlugs=True)
    cmds.refresh(force=True)


def _component(vertices, count):
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.addElements(om2.MIntArray(list(range(count))))
    return comp


def _resolve_influence_names(influence_names):
    resolved = []
    missing = []
    for raw in influence_names:
        raw = str(raw)
        if cmds.objExists(raw):
            resolved.append(raw)
            continue
        leaf = _leaf(raw)
        hits = cmds.ls(leaf, long=True) or []
        if hits:
            resolved.append(hits[0])
        else:
            missing.append(raw)
    if missing:
        raise RuntimeError("Missing influences: %s" % missing[:8])
    return resolved


def _apply_weights(mesh, skin_name, influence_names, weights):
    resolved_influences = _resolve_influence_names(influence_names)
    if cmds.objExists(skin_name):
        cmds.delete(skin_name)
    skin = cmds.skinCluster(
        resolved_influences,
        mesh,
        toSelectedBones=True,
        bindMethod=0,
        skinMethod=0,
        normalizeWeights=1,
        maximumInfluences=12,
        obeyMaxInfluences=False,
        name=skin_name,
    )[0]
    cmds.setAttr(skin + ".normalizeWeights", 1)

    sel = om2.MSelectionList()
    sel.add(skin)
    skin_obj = sel.getDependNode(0)
    fn_skin = oma2.MFnSkinCluster(skin_obj)
    mesh_dag = _dag_path(_shape(mesh))

    skin_influences = fn_skin.influenceObjects()
    name_to_logical = {}
    for path in skin_influences:
        logical = fn_skin.indexForInfluenceObject(path)
        full = path.fullPathName()
        partial = path.partialPathName()
        leaf = _leaf(partial)
        name_to_logical[full] = logical
        name_to_logical[partial] = logical
        name_to_logical[leaf] = logical

    logical_indices = []
    cols = []
    for col, raw in enumerate(influence_names):
        raw = str(raw)
        logical = name_to_logical.get(raw)
        if logical is None:
            logical = name_to_logical.get(_leaf(raw))
        if logical is None:
            continue
        logical_indices.append(int(logical))
        cols.append(col)
    if not cols:
        raise RuntimeError("No influence columns matched skinCluster %s" % skin)

    sub = np.asarray(weights[:, cols], dtype=np.float64)
    row_sum = sub.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0.0] = 1.0
    sub = sub / row_sum
    vcount = int(cmds.polyEvaluate(mesh, vertex=True))
    comp = _component(mesh, vcount)
    infl_array = om2.MIntArray(logical_indices)
    flat = om2.MDoubleArray(sub.ravel(order="C").tolist())
    fn_skin.setWeights(mesh_dag, comp, infl_array, flat, True)
    return skin


def _make_material(name, color):
    if cmds.objExists(name):
        shader = name
    else:
        shader = cmds.shadingNode("lambert", asShader=True, name=name)
        cmds.setAttr(shader + ".color", color[0], color[1], color[2], type="double3")
    sg = shader + "SG"
    if not cmds.objExists(sg):
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg)
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    return sg


def _assign_material(mesh, material_name, color):
    sg = _make_material(material_name, color)
    cmds.sets(mesh, edit=True, forceElement=sg)


def _delete_existing():
    for item in CANDIDATES:
        if cmds.objExists(item["group"]):
            cmds.delete(item["group"])
        if cmds.objExists(item["mesh"]):
            cmds.delete(item["mesh"])
        if cmds.objExists(item["error_set"]):
            cmds.delete(item["error_set"])


def _duplicate_target(name):
    dup = cmds.duplicate("A", returnRootsOnly=True, name=name)[0]
    try:
        cmds.delete(dup, constructionHistory=True)
    except Exception:
        pass
    cmds.setAttr(dup + ".translateX", 0.0)
    cmds.setAttr(dup + ".translateY", 0.0)
    cmds.setAttr(dup + ".translateZ", 0.0)
    return dup


def _face_range_vertices(data, start_face, end_face):
    counts = data["target_face_counts"].astype(int)
    offsets = data["target_face_offsets"].astype(int)
    flat = data["target_face_vertices"].astype(int)
    vertices = set()
    for face_id in range(start_face, end_face + 1):
        vertices.update(flat[offsets[face_id] : offsets[face_id] + counts[face_id]].tolist())
    return np.asarray(sorted(vertices), dtype=np.int32)


def _stat(values):
    if len(values) == 0:
        return {"count": 0}
    return {
        "count": int(len(values)),
        "mean": float(np.mean(values)),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
    }


def _metrics_for(mesh, val, target_points, source_neutral_points, source_jaw_points, target_neutral_points):
    source_tris = val["source_tris"].astype(np.int32)
    best_tri = val["best_tri"].astype(np.int32)
    best_bary = val["best_bary"].astype(np.float64)
    unsupported = val["unsupported"].astype(bool)
    low = val["low_confidence"].astype(bool)
    high = val["high_confidence"].astype(bool)
    strict = (
        val["normal_mismatch"].astype(bool)
        | val["semantic_ambiguous"].astype(bool)
        | val["topology_discontinuity"].astype(bool)
        | val["face_semantic_discontinuity"].astype(bool)
    ) & (~unsupported)

    def expected(source_points, ids):
        tris = source_tris[best_tri[ids]]
        bary = best_bary[ids]
        return (
            bary[:, 0:1] * source_points[tris[:, 0]]
            + bary[:, 1:2] * source_points[tris[:, 1]]
            + bary[:, 2:3] * source_points[tris[:, 2]]
        )

    actual_jaw = _get_points(mesh)
    groups = {
        "supported": np.where(~unsupported)[0],
        "high_confidence": np.where(high)[0],
        "low_confidence": np.where(low)[0],
        "strict_block": np.where(strict)[0],
        "mouth_red_left_9095_9150": _face_range_vertices(target_points, 9095, 9150),
        "mouth_red_mid_9263_9318": _face_range_vertices(target_points, 9263, 9318),
    }
    result = {}
    errors_by_group = {}
    for name, ids in groups.items():
        ids = ids[(~unsupported[ids])] if len(ids) else ids
        if len(ids) == 0:
            result[name] = {"count": 0}
            errors_by_group[name] = (ids, np.zeros((0,), dtype=np.float64))
            continue
        expected_neutral = expected(source_neutral_points, ids)
        expected_jaw = expected(source_jaw_points, ids)
        absolute_err = np.linalg.norm(actual_jaw[ids] - expected_jaw, axis=1)
        motion_err = np.linalg.norm(
            (actual_jaw[ids] - target_neutral_points[ids]) - (expected_jaw - expected_neutral),
            axis=1,
        )
        errors_by_group[name] = (ids, motion_err)
        result[name] = {
            "count": int(len(ids)),
            "absolute": _stat(absolute_err),
            "motion_delta": _stat(motion_err),
        }
    return result, errors_by_group


def _make_error_set(mesh, set_name, ids, errors, top_n=300):
    if cmds.objExists(set_name):
        cmds.delete(set_name)
    if len(ids) == 0:
        return {"count": 0, "set": set_name}
    order = np.argsort(errors)[::-1][: min(top_n, len(ids))]
    selected_ids = [int(ids[i]) for i in order]
    comps = ["%s.vtx[%d]" % (mesh, idx) for idx in selected_ids]
    created = cmds.sets(comps, name=set_name)
    return {"count": len(selected_ids), "set": created, "max_error": float(errors[order[0]])}


def main():
    _set_jaw(0.0)
    weights_data = np.load(str(WEIGHTS_PATH), allow_pickle=True)
    hybrid_weights_data = np.load(str(HYBRID_WEIGHTS_PATH), allow_pickle=True)
    v083_weights_data = np.load(str(V083_WEIGHTS_PATH), allow_pickle=True)
    weight_sources = {"base": weights_data, "hybrid": hybrid_weights_data, "v083": v083_weights_data}
    source_data = np.load(str(SOURCE_DATA), allow_pickle=True)
    influence_payload = json.loads(INFLUENCE_JSON_PATH.read_text(encoding="utf-8"))
    influence_names = influence_payload["influence_names"]
    _delete_existing()
    created = {}

    cmds.undoInfo(openChunk=True, chunkName="CDFDIAG_V082_weight_candidate_test")
    try:
        for item in CANDIDATES:
            mesh = _duplicate_target(item["mesh"])
            _assign_material(mesh, item["material"], item["color"])
            source_key = item.get("weights_source", "base")
            skin = _apply_weights(
                mesh,
                item["skin"],
                influence_names,
                weight_sources[source_key][item["key"]],
            )
            created[item["key"]] = {"mesh": mesh, "skinCluster": skin}

        source_neutral_points = _get_points("M_Head_base")
        target_neutral_points = {item["key"]: _get_points(item["mesh"]) for item in CANDIDATES}
        _set_jaw(25.0)
        source_jaw_points = _get_points("M_Head_base")
        report = {
            "status": "SUCCESS",
            "scene": cmds.file(q=True, sn=True),
            "weights_path": str(WEIGHTS_PATH),
            "hybrid_weights_path": str(HYBRID_WEIGHTS_PATH),
            "v083_weights_path": str(V083_WEIGHTS_PATH),
            "influence_json_path": str(INFLUENCE_JSON_PATH),
            "error_arrays_path": str(ERROR_ARRAYS_PATH),
            "jaw_pose": {"M_Jaw_A_ctrl.rotateX": 25.0},
            "candidates": {},
            "note": "Metrics compare candidate deformed A vertices against source M_Head_base barycentric correspondence under Jaw25 before visual offsets.",
        }
        selected_sets = []
        error_arrays = {}
        for item in CANDIDATES:
            metrics_by_validation = {}
            errors_by_validation = {}
            for validation_key, validation_path in VALIDATION_PATHS.items():
                val = np.load(str(validation_path), allow_pickle=True)
                metrics, errors_by_group = _metrics_for(
                    item["mesh"],
                    val,
                    source_data,
                    source_neutral_points,
                    source_jaw_points,
                    target_neutral_points[item["key"]],
                )
                ids, errors = errors_by_group["supported"]
                full_error = np.full(
                    (int(cmds.polyEvaluate(item["mesh"], vertex=True)),), np.nan, dtype=np.float64
                )
                full_error[ids] = errors
                prefix = item["key"] + "__" + validation_key
                error_arrays[prefix + "_supported_ids"] = ids.astype(np.int32)
                error_arrays[prefix + "_motion_error"] = full_error
                metrics_by_validation[validation_key] = metrics
                errors_by_validation[validation_key] = (ids, errors)

            primary_validation = item.get("primary_validation", item["key"])
            ids, errors = errors_by_validation[primary_validation]
            err_set = _make_error_set(item["mesh"], item["error_set"], ids, errors, top_n=300)
            report["candidates"][item["key"]] = {
                "mesh": item["mesh"],
                "skinCluster": item["skin"],
                "metrics": metrics_by_validation[primary_validation],
                "metrics_by_validation": metrics_by_validation,
                "error_set": err_set,
            }
            selected_sets.append(item["error_set"])

        np.savez_compressed(str(ERROR_ARRAYS_PATH), **error_arrays)

        for item in CANDIDATES:
            if cmds.objExists(item["group"]):
                cmds.delete(item["group"])
            group = cmds.group(item["mesh"], name=item["group"])
            cmds.setAttr(group + ".translateX", float(item["offset_x"]))

        if selected_sets and cmds.objExists(selected_sets[-1]):
            cmds.select(selected_sets[-1], replace=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        cmds.undoInfo(closeChunk=True)
    return report


result = main()
