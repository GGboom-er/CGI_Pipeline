"""Bounded ngSkinTools2 adapter. The oracle validates; only ng writes weights."""
from __future__ import annotations

from array import array
from heapq import nlargest
import math
import uuid

# Internal allocation limit, not a geometry merge criterion. 32M float64 values
# are 256 MB; the Proxy workflow processes one final skin at a time.
MAX_WEIGHT_VALUES = 32_000_000
TOLERANCE = 1e-7
ENGINE_FLOOR = 1e-9
ATTRS = ("normalizeWeights", "skinningMethod", "maxInfluences", "maintainMaxInfluences")


def validate_params(params):
    """Validate public input before importing a host or touching its scene."""
    if not isinstance(params, dict):
        raise ValueError("parameters must be an object")
    names = params.get("skin_clusters")
    if not isinstance(names, list) or not names or any(
        not isinstance(name, str) or not name.strip() for name in names
    ):
        raise ValueError("skin_clusters must be an explicit nonempty list of node names")
    if len(set(names)) != len(names):
        raise ValueError("skin_clusters contains duplicate targets")
    mode = params.get("mode", "inspect")
    threshold = params.get("threshold", 0.001)
    maximum = params.get("max_influences", 12)
    remove = params.get("remove_unused_influences", True)
    if mode not in ("inspect", "apply"):
        raise ValueError("mode must be inspect or apply")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not (
        0 <= threshold < 1 and math.isfinite(threshold)
    ):
        raise ValueError("threshold must be a finite number in [0, 1)")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("max_influences must be a positive integer")
    if not isinstance(remove, bool):
        raise ValueError("remove_unused_influences must be boolean")
    return {"skin_clusters": names, "mode": mode, "threshold": float(threshold),
            "max_influences": maximum, "remove_unused_influences": remove}


def expected_row(values, threshold, maximum, normalize_mode=1):
    """Independent acceptance oracle; never used to assign production weights."""
    if not values or any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("negative or nonfinite skin weights are unsupported")
    try:
        total = math.fsum(values)
    except OverflowError as exc:
        raise ValueError("nonfinite weight-sum row is unsupported") from exc
    if not math.isfinite(total) or total <= 0:
        raise ValueError("zero or nonfinite weight-sum row is unsupported")
    if normalize_mode not in (0, 1, 2):
        raise ValueError("unsupported normalizeWeights mode")
    if normalize_mode == 0 and abs(total - 1.0) > TOLERANCE:
        raise ValueError("normalizeWeights=None with nonunit row sum is unsupported")
    keep = [index for index, value in enumerate(values) if value / total > threshold]
    keep.sort(key=lambda index: (-values[index], index))
    keep = keep[:maximum]
    retained = math.fsum(values[index] for index in keep)
    if not retained:
        raise ValueError("threshold would empty a row; ng fallback assignment is unsupported")
    chosen = set(keep)
    return [value / retained if index in chosen else 0.0 for index, value in enumerate(values)]


def verify_row(before, after, threshold, maximum):
    """Accept equal-valued cap ties, but no unintended loss above ng's floor."""
    if len(before) != len(after) or any(not math.isfinite(x) or x < 0 for x in after):
        raise RuntimeError("ng returned invalid weights")
    kept = [index for index, value in enumerate(after) if value > 0]
    if not kept or len(kept) > maximum or abs(math.fsum(after) - 1) > TOLERANCE:
        raise RuntimeError("ng output violates per-vertex influence cap or normalization")
    source_sum = math.fsum(before)
    if any(before[index] / source_sum <= threshold for index in kept):
        raise RuntimeError("ng output retained a source weight at or below threshold")
    cutoff = min(before[index] for index in kept)
    for index, value in enumerate(before):
        if index in kept or value / source_sum <= threshold or value / source_sum < ENGINE_FLOOR:
            continue
        if len(kept) < maximum or value > cutoff:
            raise RuntimeError("ng unexpectedly discarded a significant source weight")
    retained = math.fsum(before[index] for index in kept)
    error = max(abs(after[index] - before[index] / retained) for index in kept)
    if error > TOLERANCE:
        raise RuntimeError("ng output changed retained influence proportions")
    return error


def _access(name, cmds, om, oma, remaining):
    names = cmds.ls(name, long=True) or []
    if len(names) != 1 or cmds.nodeType(names[0]) != "skinCluster":
        raise ValueError("target must identify exactly one skinCluster: " + name)
    name = names[0]
    selection = om.MSelectionList()
    selection.add(name)
    skin = oma.MFnSkinCluster(selection.getDependNode(0))
    outputs = skin.getOutputGeometry()
    if len(outputs) != 1:
        raise ValueError(name + ": exactly one output mesh is supported")
    shape = om.MDagPath.getAPathTo(outputs[0])
    if not shape.hasFn(om.MFn.kMesh) or shape.isInstanced():
        raise ValueError(name + ": only a non-instanced polygon mesh is supported")
    count = om.MFnMesh(shape).numVertices
    paths = skin.influenceObjects()
    if not count or not paths:
        raise ValueError(name + ": empty geometry or influence list is unsupported")
    if count * len(paths) > remaining:
        raise ValueError("batch exceeds available weight-buffer budget; process skins serially")
    component_fn = om.MFnSingleIndexedComponent()
    component = component_fn.create(om.MFn.kMeshVertComponent)
    component_fn.setCompleteData(count)
    return name, skin, shape, component, count, paths


def _influence(skin, path, cmds):
    name = path.fullPathName()
    logical = skin.indexForInfluenceObject(path)
    return {"name": name, "uuid": cmds.ls(name, uuid=True)[0], "logical_index": logical,
            "locked": bool(cmds.getAttr(name + ".liw")) if cmds.objExists(name + ".liw") else False,
            "world_matrix": cmds.getAttr(name + ".worldMatrix[0]"),
            "bind_matrix": cmds.getAttr(skin.name() + ".bindPreMatrix[{}]".format(logical))}


def _capture(name, cmds, om, oma, remaining=MAX_WEIGHT_VALUES):
    name, skin, shape, component, count, paths = _access(name, cmds, om, oma, remaining)
    weights, width = skin.getWeights(shape, component)
    if width != len(paths) or len(weights) != count * width:
        raise ValueError(name + ": unexpected physical influence mapping")
    influences = [_influence(skin, path, cmds) for path in paths]
    for physical, influence in enumerate(influences):
        influence["physical_index"] = physical
    return {"name": name, "uuid": cmds.ls(name, uuid=True)[0],
            "shape": shape.fullPathName(), "shape_uuid": cmds.ls(shape.fullPathName(), uuid=True)[0],
            "count": count, "width": width, "weights": array("d", weights),
            "blend_weights": array("d", skin.getBlendWeights(shape, component)),
            "influences": influences,
            "attrs": {attr: cmds.getAttr(name + "." + attr) for attr in ATTRS},
            "geom_matrix": cmds.getAttr(name + ".geomMatrix")}


def _rows(snapshot):
    width = snapshot["width"]
    for offset in range(0, len(snapshot["weights"]), width):
        yield snapshot["weights"][offset:offset + width]


def _zero_columns(snapshot):
    active = [False] * snapshot["width"]
    for row in _rows(snapshot):
        for index, value in enumerate(row):
            active[index] = active[index] or value > 0
    return [index for index, value in enumerate(active) if not value]


def _plan(snapshot, options):
    changed = 0
    cap = 0
    loss = {stage: {"total": 0.0, "max": 0.0} for stage in ("threshold", "cap", "combined")}
    nonzero = [False] * snapshot["width"]
    for row in _rows(snapshot):
        expected = expected_row(row, options["threshold"], options["max_influences"],
                                snapshot["attrs"]["normalizeWeights"])
        total = math.fsum(row)
        threshold_mass = math.fsum(value / total for value in row if 0 < value / total <= options["threshold"])
        cap_mass = math.fsum(value / total for index, value in enumerate(row)
                             if value / total > options["threshold"] and expected[index] == 0)
        for stage, mass in (("threshold", threshold_mass), ("cap", cap_mass), ("combined", threshold_mass + cap_mass)):
            loss[stage]["total"] += mass
            loss[stage]["max"] = max(loss[stage]["max"], mass)
        cap = max(cap, sum(value > 0 for value in row))
        changed += any((old > 0 and new == 0) or abs(old - new) > TOLERANCE
                       for old, new in zip(row, expected))
        for index, value in enumerate(row):
            nonzero[index] = nonzero[index] or value > 0
    snapshot["changed_vertices"] = changed
    snapshot["before_cap"] = cap
    snapshot["zero_influences"] = [i for i, active in enumerate(nonzero) if not active]
    snapshot["planned_removed_mass"] = {stage: {**stats, "mean": stats["total"] / snapshot["count"]}
                                        for stage, stats in loss.items()}
    if changed and any(info["locked"] and active for info, active in zip(snapshot["influences"], nonzero)):
        raise ValueError(snapshot["name"] + ": ng filters do not preserve nonzero locked influences")


def _preflight(snapshot, options, cmds, remove_data):
    name = snapshot["name"]
    if remove_data.list_custom_nodes_for_mesh(name):
        raise ValueError(name + ": existing ng layer data is unsupported; user layers are preserved")
    if cmds.listConnections(name + ".weightList", source=True, destination=False, plugs=True):
        raise ValueError(name + ": driven or animated weight plugs are unsupported")
    _plan(snapshot, options)
    if options["mode"] != "apply":
        return
    if cmds.referenceQuery(name, isNodeReferenced=True) or cmds.lockNode(name, query=True, lock=True)[0]:
        raise ValueError(name + ": referenced or locked skinCluster is unsupported")
    if cmds.getAttr(name + ".weightList", lock=True):
        raise ValueError(name + ": locked weightList is unsupported")
    desired = {"maxInfluences": options["max_influences"], "maintainMaxInfluences": True}
    for attr, value in desired.items():
        plug = name + "." + attr
        if snapshot["attrs"][attr] != value and not cmds.getAttr(plug, settable=True):
            raise ValueError(plug + ": cannot set the requested future influence constraint")


def _node_ids(remove_data, cmds):
    nodes = remove_data.list_custom_nodes()
    return set(cmds.ls(nodes, uuid=True) or []) if nodes else set()


def _check_metadata(before, after, cmds, restored=False):
    for key in ("uuid", "shape_uuid", "count", "geom_matrix", "blend_weights"):
        if before[key] != after[key]:
            raise RuntimeError(before["name"] + ": changed protected skin metadata " + key)
    attrs = ATTRS if restored else ATTRS[:2]
    if any(before["attrs"][attr] != after["attrs"][attr] for attr in attrs):
        raise RuntimeError(before["name"] + ": changed protected skin settings")
    final = {info["uuid"]: info for info in after["influences"]}
    for info in before["influences"]:
        if (cmds.ls(info["name"], uuid=True) or []) != [info["uuid"]]:
            raise RuntimeError("original influence node was removed or replaced: " + info["name"])
        if cmds.getAttr(info["name"] + ".worldMatrix[0]") != info["world_matrix"]:
            raise RuntimeError("original influence transform changed: " + info["name"])
        if info["uuid"] in final and any(info[key] != final[info["uuid"]][key]
                                         for key in ("bind_matrix", "logical_index", "locked")):
            raise RuntimeError("influence binding metadata changed: " + info["name"])


def _aligned_rows(before, after):
    lookup = {info["uuid"]: i for i, info in enumerate(after["influences"])}
    indices = [lookup.get(info["uuid"]) for info in before["influences"]]
    original_ids = {info["uuid"] for info in before["influences"]}
    if any(info["uuid"] not in original_ids for info in after["influences"]):
        raise RuntimeError("unexpected influence was added")
    for original, actual in zip(_rows(before), _rows(after)):
        yield original, [actual[index] if index is not None else 0.0 for index in indices]


def extra_floor_mass(before, after, threshold, maximum):
    """Additional removed source mass beyond threshold/top-K, excluding valid cap loss."""
    slots = maximum - sum(value > 0 for value in after)
    if slots <= 0:
        return 0.0
    total = math.fsum(before)
    omitted = (value / total for value, actual in zip(before, after)
               if actual == 0 and threshold < value / total < ENGINE_FLOOR)
    return math.fsum(nlargest(slots, omitted))


def _verify(before, after, options, cmds, restored=False):
    _check_metadata(before, after, cmds, restored)
    if restored and before["influences"] != after["influences"]:
        raise RuntimeError("rollback did not restore original influence identities and mapping")
    maximum_error = 0.0
    floor_loss = {"max": 0.0, "total": 0.0}
    for original, actual in _aligned_rows(before, after):
        if restored or not before["changed_vertices"]:
            if list(original) != actual:
                raise RuntimeError("original weights were not preserved")
        else:
            maximum_error = max(maximum_error, verify_row(
                original, actual, options["threshold"], options["max_influences"]))
            mass = extra_floor_mass(original, actual, options["threshold"], options["max_influences"])
            floor_loss["max"] = max(floor_loss["max"], mass)
            floor_loss["total"] += mass
    after["engine_floor_removed_mass"] = floor_loss
    return maximum_error


def _apply_skin(before, options, cmds, om, oma, init_layers, remove_data):
    name = before["name"]
    if before["changed_vertices"]:
        # ng applies its cap before threshold. Bake a threshold-only pass first
        # so the public threshold continues to refer to the source effective row.
        if options["threshold"] > 0:
            layers = init_layers(name)
            layers.add("CGI threshold cleanup", force_empty=False)
            layers.influence_limit_per_vertex = before["width"]
            layers.prune_weights_filter_threshold = options["threshold"]
            remove_data.remove_custom_nodes(interactive=False, meshes=[before["shape"]])
        layers = init_layers(name)
        layers.add("CGI influence cleanup", force_empty=False)
        layers.prune_weights_filter_threshold = 0.0
        layers.influence_limit_per_vertex = options["max_influences"]
        remove_data.remove_custom_nodes(interactive=False, meshes=[before["shape"]])
    baked = _capture(name, cmds, om, oma)
    zero = [baked["influences"][index]["uuid"] for index in _zero_columns(baked)]
    removed = []
    if options["remove_unused_influences"] and zero:
        cmds.skinCluster(name, edit=True, removeUnusedInfluence=True)
        final = _capture(name, cmds, om, oma)
        removed = [info["uuid"] for info in baked["influences"]
                   if info["uuid"] not in {item["uuid"] for item in final["influences"]}]
        if set(removed) != set(zero):
            raise RuntimeError(name + ": native unused-influence removal changed nonzero relationships or left zero ones")
    for attr, value in (("maxInfluences", options["max_influences"]), ("maintainMaxInfluences", True)):
        if cmds.getAttr(name + "." + attr) != value:
            cmds.setAttr(name + "." + attr, value)
    after = _capture(name, cmds, om, oma)
    error = _verify(before, after, options, cmds)
    if after["attrs"]["maxInfluences"] != options["max_influences"] or not after["attrs"]["maintainMaxInfluences"]:
        raise RuntimeError(name + ": future influence constraint did not persist")
    return after, removed, error


def _summary(before, after=None, removed=(), error=None):
    changed = bool(after and (before["changed_vertices"] or removed or before["attrs"] != after["attrs"]))
    return {"skin_cluster": before["name"], "mesh": before["shape"],
            "vertex_count": before["count"], "influences_before": before["width"],
            "influences_after": after["width"] if after else None,
            "max_nonzero_before": before["before_cap"],
            "max_nonzero_after": max((sum(x > 0 for x in row) for row in _rows(after)), default=0) if after else None,
            "changed_vertices": before["changed_vertices"],
            "changed": changed,
            "engine_applied": bool(after and before["changed_vertices"]),
            "zero_influences_before": len(before["zero_influences"]),
            "planned_removed_mass": before.get("planned_removed_mass"),
            "engine_floor_removed_mass": after.get("engine_floor_removed_mass", {"max": 0.0, "total": 0.0}) if after else None,
            "loss_metric": "removed source effective weight mass; not a deformation error bound",
            "removed_influence_uuids": list(removed), "max_proportion_error": error,
            "normalize_mode": before["attrs"]["normalizeWeights"],
            "skinning_method": before["attrs"]["skinningMethod"],
            "physical_mapping": [{key: info[key] for key in ("name", "uuid", "logical_index", "physical_index")}
                                 for info in (after or before)["influences"]],
            "verified": after is not None}


def _restored(snapshots, options, baseline_ng, cmds, om, oma, remove_data, selection):
    if _node_ids(remove_data, cmds) != baseline_ng:
        raise RuntimeError("ng custom-node set differs from the pre-operation baseline")
    for before in snapshots:
        _verify(before, _capture(before["name"], cmds, om, oma), options, cmds, restored=True)
    if (cmds.ls(selection=True, long=True) or []) != selection:
        raise RuntimeError("original selection was not restored")


def _needs_write(before, options):
    return bool(before["changed_vertices"] or
                (options["remove_unused_influences"] and before["zero_influences"]) or
                before["attrs"]["maxInfluences"] != options["max_influences"] or
                not before["attrs"]["maintainMaxInfluences"])


def _transaction(snapshots, options, cmds, om, oma, init_layers, remove_data):
    if not any(_needs_write(before, options) for before in snapshots):
        return [_summary(before, before, error=0.0) for before in snapshots]
    baseline_ng = _node_ids(remove_data, cmds)
    selection = cmds.ls(selection=True, long=True) or []
    chunk = "CGI_ng_cleanup_" + uuid.uuid4().hex
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("Maya undo must be enabled for apply")
    results = []
    failure = None
    cmds.undoInfo(openChunk=True, chunkName=chunk)
    try:
        for before in snapshots:
            after, removed, error = _apply_skin(before, options, cmds, om, oma, init_layers, remove_data)
            results.append(_summary(before, after, removed, error))
        if _node_ids(remove_data, cmds) != baseline_ng:
            raise RuntimeError("ng cleanup left unexpected custom nodes or removed existing nodes")
    except Exception as exc:
        failure = exc
    finally:
        try:
            if (cmds.ls(selection=True, long=True) or []) != selection:
                cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
            if (cmds.ls(selection=True, long=True) or []) != selection:
                raise RuntimeError("could not restore original selection")
        except Exception as exc:
            failure = failure or exc
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception as exc:
            failure = failure or exc
    if failure is None:
        return results
    try:
        try:
            _restored(snapshots, options, baseline_ng, cmds, om, oma, remove_data, selection)
        except Exception:
            if cmds.undoInfo(query=True, undoName=True) != chunk:
                raise RuntimeError("failed transaction has no matching native undo entry")
            cmds.undo()
            _restored(snapshots, options, baseline_ng, cmds, om, oma, remove_data, selection)
    except Exception as rollback_error:
        raise RuntimeError("rollback_failed: {}; original error: {}".format(rollback_error, failure)) from failure
    raise RuntimeError("ng cleanup failed; native undo rollback verified: " + str(failure)) from failure


def run(params):
    """Inspect explicit targets, or atomically apply third-party weight filtering."""
    options = validate_params(params)
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma
    from ngSkinTools2.api import PaintTool, init_layers
    from ngSkinTools2.operations import removeLayerData
    if not cmds.pluginInfo("ngSkinTools2", query=True, loaded=True):
        raise ImportError("ngSkinTools2 must already be installed and loaded")
    version = cmds.pluginInfo("ngSkinTools2", query=True, version=True)
    if version != "2.4.0":
        raise ValueError("only ngSkinTools2 2.4.0 has been verified; found " + version)
    if PaintTool.is_painting():
        raise ValueError("active ng paint session is unsupported; finish painting first")
    snapshots = []
    remaining = MAX_WEIGHT_VALUES
    for name in options["skin_clusters"]:
        snapshot = _capture(name, cmds, om, oma, remaining)
        remaining -= len(snapshot["weights"])
        if any(old["uuid"] == snapshot["uuid"] or old["shape_uuid"] == snapshot["shape_uuid"] for old in snapshots):
            raise ValueError("duplicate skinCluster or shared target mesh in batch")
        _preflight(snapshot, options, cmds, removeLayerData)
        snapshots.append(snapshot)
    skins = [_summary(before) for before in snapshots]
    if options["mode"] == "apply":
        skins = _transaction(snapshots, options, cmds, om, oma, init_layers, removeLayerData)
    return {"engine": "ngSkinTools2", "engine_version": version, "mode": options["mode"],
            "skins": skins, "threshold": options["threshold"], "max_influences": options["max_influences"],
            "warnings": ["ngSkinTools2 may discard effective weights below its 1e-9 numerical floor; acceptance tolerance is 1e-7"],
            "engine_numerical_floor": ENGINE_FLOOR, "acceptance_tolerance": TOLERANCE}
