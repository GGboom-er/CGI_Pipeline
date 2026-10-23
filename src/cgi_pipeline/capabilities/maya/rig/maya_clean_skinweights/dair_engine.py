"""Native-Maya adapter for the independently authored DAIR-inspired adaptation."""
from __future__ import annotations

from array import array
import math
import time
import uuid

from . import ng_engine


def validate_params(params):
    options = ng_engine.validate_params(params)
    rings = params.get("neighbor_rings", 1)
    delta = params.get("neighbor_max_delta", .02)
    if isinstance(rings, bool) or not isinstance(rings, int) or rings not in (0, 1, 2):
        raise ValueError("neighbor_rings must be 0, 1 or 2")
    if isinstance(delta, bool) or not isinstance(delta, (int, float)) or not (0 < delta <= 1 and math.isfinite(delta)):
        raise ValueError("neighbor_max_delta must be finite and in (0, 1]")
    options.update(neighbor_rings=rings, neighbor_max_delta=float(delta))
    workdir = params.get("solver_workdir")
    if workdir is not None and (not isinstance(workdir, str) or not workdir.strip()):
        raise ValueError("solver_workdir must be a nonempty absolute managed workspace path")
    if workdir is not None:
        options["solver_workdir"] = workdir
    return options


def require_deps():
    from .dair_solver import require_deps as probe
    return probe()


def preflight_sources(skins):
    """Read source compatibility only; geometry inheritance validates weights.

No source getWeights, input points/topology arrays, bind-matrix capture or pose
sampling occurs. Final output inspect/apply still performs full weight checks.
"""
    started = time.perf_counter()
    if not isinstance(skins, list) or any(not isinstance(name, str) or not name.strip() for name in skins):
        raise ValueError("source skins must be an explicit list of skinCluster names")
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma
    from . import dair_scene as scene
    rows, identities, shapes = [], set(), set()
    remaining = ng_engine.MAX_WEIGHT_VALUES
    for name in skins:
        name, _, shape, _, count, paths = ng_engine._access(name, cmds, om, oma, remaining)
        remaining -= count * len(paths)
        identity, shape_id = cmds.ls(name, uuid=True)[0], cmds.ls(shape.fullPathName(), uuid=True)[0]
        if identity in identities or shape_id in shapes:
            raise ValueError("duplicate skinCluster or shared source mesh in batch")
        identities.add(identity)
        shapes.add(shape_id)
        snapshot = {"name": name, "attrs": {"skinningMethod": cmds.getAttr(name + ".skinningMethod")}}
        scene.preflight(snapshot, {"mode": "inspect"}, cmds)
        rows.append({"skin_cluster": name, "mesh": shape.fullPathName(), "vertex_count": count,
                     "influence_count": len(paths), "uuid": identity})
    return {"skins": rows, "timing_seconds": {"source_preflight": time.perf_counter() - started},
            "weight_validation": "deferred_to_geometry_inheritance_and_final_output"}


def _record_time(timings, stage, started):
    timings[stage] = timings.get(stage, 0.0) + time.perf_counter() - started


def _prepare(name, options, cmds, om, oma, remaining, timings=None):
    import numpy as np
    from . import dair_data as data
    from . import dair_scene as scene
    timings = {} if timings is None else timings
    started = time.perf_counter()
    before = ng_engine._capture(name, cmds, om, oma, remaining)
    _record_time(timings, "weight_capture", started)
    started = time.perf_counter()
    scene.preflight(before, options, cmds)
    weights = np.asarray(before["weights"]).reshape((before["count"], before["width"]))
    if before["attrs"]["normalizeWeights"] == 0 and not np.allclose(weights.sum(axis=1), 1, atol=1e-7, rtol=0):
        raise ValueError(name + ": normalizeWeights=None requires unit input rows")
    try:
        original, clean, threshold_rows, cap = data.tiered_filter(
            weights, options["threshold"], options["max_influences"])
        active = np.union1d(threshold_rows, cap)
    except ValueError as exc:
        raise ValueError(name + ": " + str(exc)) from exc
    before["numeric_correction_rows"] = int(np.count_nonzero(np.any(weights < 0, axis=1)))
    before["changed_vertices"] = len(active)
    before["before_cap"] = int(np.count_nonzero(original, axis=1).max())
    before["zero_influences"] = np.flatnonzero(~np.any(weights > 0, axis=0)).tolist()
    before["metrics"] = {"sampled_pose_count": None, "compressed_pose_groups": None,
                         "changed_vertices": len(active), "primary_count": len(active),
                         "neighbor_candidates": None if len(active) and options["neighbor_rings"] else 0,
                         "neighbor_changed_count": None if len(active) else 0,
                         "max_neighbor_delta": None if len(active) else 0.0,
                         "neighbor_rings": options["neighbor_rings"], "neighbor_max_delta": options["neighbor_max_delta"],
                         "threshold": options["threshold"], "max_influences": options["max_influences"],
                         "baseline_error": None, "optimized_error": None,
                         "geometry_pose_deferred": True}
    before["expected"] = original
    before["metrics"].update(threshold_count=len(threshold_rows), cap_count=len(cap),
        cap_scan_skipped=before["width"] <= options["max_influences"], solver_vertex_count=0)
    before["expected"] = clean
    _record_time(timings, "preflight_checks", started)
    if options["mode"] == "apply" and len(active):
        started = time.perf_counter()
        points, edges = scene.input_mesh(before, cmds, om, oma)
        if len(cap):
            pivots = scene.sample_pivots(before, cmds)
            masks, moments, pose_count = data.compress_poses(
                [info["name"] for info in before["influences"]], pivots)
        _record_time(timings, "geometry_and_poses", started)
        from .dair_solver import solve
        started = time.perf_counter()
        if len(cap):
            solved, metrics = solve(points, edges, weights, masks, moments, pose_count, {**options, "tiered": True})
        else:
            solved, neighbors = data.smooth_cleanup_delta(original, clean, edges, threshold_rows,
                options["neighbor_rings"], options["max_influences"], options["threshold"], options["neighbor_max_delta"])
            deltas = np.max(np.abs(solved[neighbors] - original[neighbors]), axis=1) if len(neighbors) else np.empty(0)
            metrics = {**before["metrics"], "neighbor_candidates": len(neighbors),
                "neighbor_changed_count": int(np.count_nonzero(deltas > data.EPSILON)),
                "max_neighbor_delta": float(deltas.max()) if len(deltas) else 0.,
                "changed_vertices": int(np.count_nonzero(np.any(solved != original, axis=1))),
                "numeric_solver_skipped": True}
        _record_time(timings, "numeric_solve", started)
        before["expected"] = solved
        before["metrics"] = metrics
        before["changed_vertices"] = metrics["changed_vertices"]
        before["metrics"]["geometry_pose_deferred"] = False
    else:
        started = time.perf_counter()
        scene.inspect_input(before, cmds, om, oma)
        _record_time(timings, "inspect_geometry_checks", started)
    return before


def _restore_locks(snapshots, cmds):
    for before in snapshots:
        for info in before["influences"]:
            plug = info["name"] + ".liw"
            if cmds.objExists(plug) and bool(cmds.getAttr(plug)) != info["locked"]:
                cmds.setAttr(plug, info["locked"])


def _release_locks(snapshots, cmds):
    for before in snapshots:
        if before["changed_vertices"] or before.get("numeric_correction_rows", 0):
            # The solved matrix owns this edit; an older host editing cap must
            # not silently prune it before the requested final cap is applied.
            if cmds.getAttr(before["name"] + ".maintainMaxInfluences"):
                cmds.setAttr(before["name"] + ".maintainMaxInfluences", False)
            for info in before["influences"]:
                if info["locked"]:
                    cmds.setAttr(info["name"] + ".liw", False)


def _summarize(before, after=None, removed=(), error=None):
    return {"skin_cluster": before["name"], "mesh": before["shape"],
            "vertex_count": before["count"], "influences_before": before["width"],
            "influences_after": after["width"] if after else None,
            "max_nonzero_before": before["before_cap"],
            "max_nonzero_after": max((sum(value > 0 for value in row)
                                      for row in ng_engine._rows(after)), default=0) if after else None,
            "changed_vertices": before["changed_vertices"],
            "zero_influences_before": len(before["zero_influences"]),
            "removed_influence_uuids": list(removed), "max_write_error": error,
            "verified": after is not None, "normalize_mode": before["attrs"]["normalizeWeights"],
            "metrics": before["metrics"]}


def _cleanup(before, options, cmds, om, oma):
    baked = ng_engine._capture(before["name"], cmds, om, oma)
    zero = {baked["influences"][index]["uuid"] for index in ng_engine._zero_columns(baked)}
    removed = []
    if options["remove_unused_influences"] and zero:
        cmds.skinCluster(before["name"], edit=True, removeUnusedInfluence=True)
        remaining = _influence_ids(before["name"], om, oma)
        removed = [info["uuid"] for info in baked["influences"] if info["uuid"] not in remaining]
        if set(removed) != zero:
            raise RuntimeError("native unused-influence removal changed a nonzero relation or left zero relations")
    for attr, value in (("maxInfluences", options["max_influences"]), ("maintainMaxInfluences", True)):
        if cmds.getAttr(before["name"] + "." + attr) != value:
            cmds.setAttr(before["name"] + "." + attr, value)
    return removed


def _influence_ids(name, om, oma):
    selection = om.MSelectionList()
    selection.add(name)
    skin = oma.MFnSkinCluster(selection.getDependNode(0))
    return {om.MFnDependencyNode(path.node()).uuid().asString() for path in skin.influenceObjects()}


def _verify_restored(snapshots, options, selection, cmds, om, oma):
    from . import dair_scene as scene
    for before in snapshots:
        after = ng_engine._capture(before["name"], cmds, om, oma)
        scene.verify(before, after, before["expected"], options, cmds, restored=True)
    if (cmds.ls(selection=True, long=True) or []) != selection:
        raise RuntimeError("native undo did not restore selection")


def _needs_write(before, options):
    return bool(before["changed_vertices"] or before.get("numeric_correction_rows", 0) or
                (options["remove_unused_influences"] and before["zero_influences"]) or
                before["attrs"]["maxInfluences"] != options["max_influences"] or
                not before["attrs"]["maintainMaxInfluences"])


def _transaction(snapshots, options, cmds, om, oma, manage_undo=True, timings=None):
    from . import dair_scene as scene
    from .undoable_weights import write_weights
    timings = {} if timings is None else timings
    if not any(_needs_write(before, options) for before in snapshots):
        results = []
        for before in snapshots:
            started = time.perf_counter()
            after = ng_engine._capture(before["name"], cmds, om, oma)
            error = scene.verify(before, after, before["expected"], options, cmds)
            results.append(_summarize(before, after, error=error))
            _record_time(timings, "final_verify", started)
        return results
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("Maya undo must be enabled for apply")
    selection = cmds.ls(selection=True, long=True) or []
    chunk = "CGI_dair_cleanup_" + uuid.uuid4().hex
    failure = None
    results = []
    removed = {}
    if manage_undo:
        cmds.undoInfo(openChunk=True, chunkName=chunk)
    try:
        started = time.perf_counter()
        _release_locks(snapshots, cmds)
        records = [weight_record(before) for before in snapshots
                   if before["changed_vertices"] or before.get("numeric_correction_rows", 0)]
        if records:
            write_weights(records)
        _record_time(timings, "weight_write", started)
        for before in snapshots:
            started = time.perf_counter()
            removed[before["uuid"]] = _cleanup(before, options, cmds, om, oma)
            _record_time(timings, "cleanup", started)
        _restore_locks(snapshots, cmds)
        for before in snapshots:
            started = time.perf_counter()
            after = ng_engine._capture(before["name"], cmds, om, oma)
            error = scene.verify(before, after, before["expected"], options, cmds)
            if (after["attrs"]["maxInfluences"] != options["max_influences"] or
                    not after["attrs"]["maintainMaxInfluences"]):
                raise RuntimeError("requested future influence cap did not persist")
            results.append(_summarize(before, after, removed[before["uuid"]], error))
            _record_time(timings, "final_verify", started)
    except Exception as exc:
        failure = exc
    finally:
        try:
            _restore_locks(snapshots, cmds)
            if (cmds.ls(selection=True, long=True) or []) != selection:
                cmds.select(selection, replace=True) if selection else cmds.select(clear=True)
        except Exception as exc:
            failure = failure or exc
        if manage_undo:
            cmds.undoInfo(closeChunk=True)
    if failure is None:
        return results
    if not manage_undo:
        raise RuntimeError("dair cleanup failed inside caller-owned transaction; caller must rollback: " + str(failure)) from failure
    try:
        try:
            _verify_restored(snapshots, options, selection, cmds, om, oma)
        except Exception:
            if cmds.undoInfo(query=True, undoName=True) != chunk:
                raise RuntimeError("failed transaction has no matching native undo entry")
            cmds.undo()
            _verify_restored(snapshots, options, selection, cmds, om, oma)
    except Exception as rollback_error:
        raise RuntimeError("rollback_failed: {}; original error: {}".format(rollback_error, failure)) from failure
    raise RuntimeError("dair cleanup failed; native undo rollback verified: " + str(failure)) from failure


def weight_record(before):
    """Only changed rows cross the undoable OpenMaya write boundary."""
    import numpy as np
    original = np.asarray(before["weights"]).reshape((before["count"], before["width"]))
    indices = np.flatnonzero(np.any(original != before["expected"], axis=1))
    # Default to the simpler full buffer when most vertices change; this is a
    # policy, not a claim of a measured universal break-even point.
    subset = len(indices) < before["count"] / 2
    chosen = indices if subset else np.arange(before["count"])
    return {"name": before["name"], "uuid": before["uuid"], "count": before["count"],
        **({"vertex_indices": indices.tolist()} if subset else {}),
        "influence_uuids": [info["uuid"] for info in before["influences"]],
        "before": array("d", original[chosen].ravel()),
        "after": array("d", before["expected"][chosen].ravel())}


def run(params, manage_undo=True):
    """Inspect targets without solving/writing, or solve then apply atomically.

manage_undo=False is for an existing native outer transaction; that caller owns
rollback. Public API callers always use the default independent transaction.
"""
    started = time.perf_counter()
    timings = {key: 0.0 for key in ("dependency_probe", "weight_capture", "preflight_checks",
        "inspect_geometry_checks", "geometry_and_poses", "numeric_solve", "weight_write", "cleanup", "final_verify")}
    options = validate_params(params)
    dependencies = require_deps()
    _record_time(timings, "dependency_probe", started)
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma
    from . import dair_data as data
    snapshots = []
    remaining = ng_engine.MAX_WEIGHT_VALUES
    for name in options["skin_clusters"]:
        before = _prepare(name, options, cmds, om, oma, remaining, timings)
        if any(old["uuid"] == before["uuid"] or old["shape_uuid"] == before["shape_uuid"] for old in snapshots):
            raise ValueError("duplicate skinCluster or shared target mesh in batch")
        snapshots.append(before)
        remaining -= len(before["weights"])
    solved_at = time.perf_counter()
    skins = [_summarize(before) for before in snapshots]
    if options["mode"] == "apply":
        skins = _transaction(snapshots, options, cmds, om, oma, manage_undo, timings)
    finished = time.perf_counter()
    return {"engine": "DAIR-inspired independent adaptation", "method": "dair_inspired",
            "algorithm_version": data.ALGORITHM_VERSION, "mode": options["mode"],
            "dependencies": dependencies, "skins": skins,
            "threshold": options["threshold"], "max_influences": options["max_influences"],
            "neighbor_rings": options["neighbor_rings"], "neighbor_max_delta": options["neighbor_max_delta"],
            "timing_seconds": {"capture_and_solve": solved_at - started,
                               "apply_and_verify": finished - solved_at, "total": finished - started, **timings},
            "sampling": {"angles_degrees": list(data.ANGLES_DEGREES), "axes": 3,
                         "ancestor_policy": "influence bind pivots; ancestors anchored by current neutral DAG offsets"},
            "warnings": ["Independent adaptation, not the original author's DAIR implementation.",
                         "Synthetic bind-skeleton local-search energy is not a bound for unseen animation.",
                         "Threshold excludes original source influences and constrains every fitted positive output weight strictly above it; retained ratios may change.",
                         "Fixed-support QPs use tau plus a 1e-10 (or 32-ULP) round-trip margin capped by 1/support_count; neighbors retain feasible original support and box bounds."]}


def finalize(params):
    """Post-combine read/cleanup only: never repeats source deformation fitting."""
    import numpy as np
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma
    from . import dair_scene as scene
    options = validate_params(params)
    started, rows = time.perf_counter(), []
    for name in options["skin_clusters"]:
        before = ng_engine._capture(name, cmds, om, oma)
        weights = np.asarray(before["weights"]).reshape((-1, before["width"]))
        before.update(expected=weights, before_cap=int(np.count_nonzero(weights, axis=1).max()),
            changed_vertices=0, zero_influences=np.flatnonzero(~np.any(weights > 0, axis=0)).tolist(),
            metrics={"numeric_solver_skipped": True, "phase": "post_combine_verify"})
        scene.verify(before, before, weights, options, cmds)
        removed = _cleanup(before, options, cmds, om, oma)
        after = ng_engine._capture(name, cmds, om, oma)
        error = scene.verify(before, after, weights, options, cmds)
        rows.append(_summarize(before, after, removed, error))
    return {"skins": rows, "max_influences": options["max_influences"], "threshold": options["threshold"],
            "timing_seconds": {"total": time.perf_counter()-started}, "solver": "skipped"}
