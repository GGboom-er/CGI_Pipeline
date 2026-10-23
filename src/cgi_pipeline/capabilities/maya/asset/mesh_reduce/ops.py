"""Reusable batch mesh reduction and compatible Combine Skinning operations.

These functions never open/save scenes. Sources remain unless a combine group
explicitly sets consume_inputs=True for its already-derived inputs.
Call on Maya's main thread. Reduce is lossy; exact checks start at the reduced
construction mesh, while native combine is checked against ordered inputs.
"""

from __future__ import annotations

import math
import re

from . import geometry as geo
from . import skin


REDUCE = dict(version=1, termination=0, keepQuadsWeight=1.0, sharpness=1.0,
              keepBorder=True, keepBorderWeight=0.5, keepMapBorder=True, keepMapBorderWeight=0.5,
              keepColorBorder=True, keepColorBorderWeight=0.5,
              keepFaceGroupBorder=True, keepFaceGroupBorderWeight=0.5,
              keepHardEdge=True, keepHardEdgeWeight=0.5,
              keepCreaseEdge=True, keepCreaseEdgeWeight=0.5,
              replaceOriginal=True, constructionHistory=True)
_READ_CONTEXT = object()


def validate_entry(entry):
    if not isinstance(entry, dict):
        raise ValueError("Each entry must be an object")
    ratio = entry.get("keep_face_ratio", 1.0)
    if isinstance(ratio, bool) or not isinstance(ratio, (float, int)) or not math.isfinite(ratio) or not 0 < ratio <= 1:
        raise ValueError("keep_face_ratio must be finite in (0,1]")
    preserve = entry.get("preserve_skin", "auto")
    if preserve != "auto" and not isinstance(preserve, bool):
        raise ValueError("preserve_skin must be auto, true, or false")
    _name(entry.get("output_name"))
    return float(ratio), preserve


def _name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:]*", name):
        raise ValueError("output_name must be a literal Maya leaf name")
    return name


def _preflight(source, expected_uuid, transfer_skin=True):
    cmds, _om, _oma = geo.modules()
    shape = geo.resolve(source, expected_uuid)
    source_skin = geo.protect_dependencies(source, shape)
    context = skin.binding_context(source_skin, shape) if source_skin and transfer_skin else None
    snapshot = geo.snapshot(source, source_skin)
    return {"source": source, "shape": shape, "skin": context, "snapshot": snapshot,
            "parent": (cmds.listRelatives(source, parent=True, fullPath=True) or [None])[0]}


def _cache_guard(source, expected_uuid, context=_READ_CONTEXT):
    """Metadata guard for operation-owned outputs, never a general edit detector."""
    cmds, _om, _oma = geo.modules()
    shape = geo.resolve(source, expected_uuid)
    native_skin = geo.protect_dependencies(source, shape)
    if context is _READ_CONTEXT:
        context = skin.binding_context(native_skin, shape, require_rest=False) if native_skin else None
    return {"source": source, "shape": shape, "shape_uuid": cmds.ls(shape, uuid=True)[0],
            "frame": cmds.currentTime(query=True),
            "matrix": cmds.xform(source, query=True, matrix=True, worldSpace=True),
            "parent": (cmds.listRelatives(source, parent=True, fullPath=True) or [None])[0],
            "visibility": geo.visibility_signature(source), "skin": context,
            "skin_uuid": cmds.ls(native_skin, uuid=True)[0] if native_skin else "",
            "history": [(node, cmds.ls(node, uuid=True)[0]) for node in
                        (cmds.listHistory(shape, pruneDagObjects=True) or [])]}


def _cache_option(prepared_cache, manage_undo):
    if prepared_cache is not None and (not isinstance(prepared_cache, dict) or manage_undo):
        raise ValueError("prepared_cache is an internal caller-owned transaction option")


def _combine_preflight(source, identity, prepared_cache):
    cached = prepared_cache.get(identity) if prepared_cache is not None else None
    if cached is None:
        return _preflight(source, identity)
    if cached["guard"] != _cache_guard(source, identity):
        raise RuntimeError("Prepared geometry changed before combine: " + source)
    return dict(cached["prepared"])


def _names_available(names):
    cmds, _om, _oma = geo.modules()
    if len(names) != len(set(names)):
        raise ValueError("Output names must be unique within the batch")
    for name in names:
        if cmds.objExists(name):
            raise ValueError("Output name already exists: " + name)


def _shape(node):
    cmds, _om, _oma = geo.modules()
    return cmds.listRelatives(node, shapes=True, noIntermediate=True,
                              fullPath=True, type="mesh")[0]


def _copy(source, name):
    cmds, _om, _oma = geo.modules()
    copied = cmds.duplicate(source, name=name, returnRootsOnly=True, upstreamNodes=False,
                            inputConnections=False, instanceLeaf=False)[0]
    copied = cmds.ls(copied, long=True)[0]
    for shape in cmds.listRelatives(copied, shapes=True, fullPath=True) or []:
        if cmds.getAttr(shape + ".intermediateObject"):
            cmds.delete(shape)
    if cmds.listConnections(_shape(copied) + ".inMesh", source=True, destination=False):
        raise RuntimeError("Static duplicate retained an input history connection: " + copied)
    return copied


def _result_row(prepared, output, actual, checks, **extra):
    original = prepared["snapshot"]
    return {"source": original["source"], "source_uuid": original["uuid"],
            "result": output, "result_uuid": actual["uuid"],
            "skin_cluster": actual["skin_cluster"], "before": geo.summary(original),
            "after": geo.summary(actual), "checks": checks, **extra}


def _restore_selection(cmds, selection):
    live = [node for node in selection if cmds.objExists(node)]
    cmds.select(live, replace=True) if live else cmds.select(clear=True)


def _verify_sources(prepared, hashes):
    for row in prepared:
        if row.get("consume"):
            continue
        actual = geo.snapshot(row["source"], row["snapshot"]["skin_cluster"])
        if geo.fingerprint(actual) != hashes[row["source"]]:
            raise RuntimeError("Source changed during geometry operation: " + row["source"])


def _transaction(label, prepared, operation, character, manage_undo=True):
    cmds, _om, _oma = geo.modules()
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("Maya undo must be enabled for geometry operations")
    before_nodes = set(cmds.ls(uuid=True) or [])
    before_selection = cmds.ls(selection=True, long=True) or []
    original_hashes = {row["source"]: geo.fingerprint(row["snapshot"]) for row in prepared}
    consumes = any(row.get("consume", False) for row in prepared)
    if not manage_undo:
        try:
            rows = operation()
            _verify_sources(prepared, original_hashes)
        finally:
            _restore_selection(cmds, before_selection)
        return {"rows": rows, "character": character, "transaction": {
            "undo_chunk": "caller_owned", "sources_unchanged": not consumes,
            "selection_restored": True, "source_deletion": consumes}}
    error = None
    marker = None
    rows = None
    cmds.undoInfo(openChunk=True, chunkName=label)
    try:
        marker = cmds.createNode("network", name="cgi_mesh_transaction")
        rows = operation()
        _verify_sources(prepared, original_hashes)
        cmds.delete(marker)
    except Exception as exc:
        error = exc
    finally:
        try:
            _restore_selection(cmds, before_selection)
        finally:
            cmds.undoInfo(closeChunk=True)
    if error is not None:
        if marker is not None:
            cmds.undo()
        restored = set(cmds.ls(uuid=True) or []) == before_nodes
        for row in prepared:
            actual = geo.snapshot(row["source"], row["snapshot"]["skin_cluster"])
            restored = restored and geo.fingerprint(actual) == original_hashes[row["source"]]
        raise RuntimeError(f"{label} failed; rollback_verified={restored}: {error}") from error
    return {"rows": rows, "character": character,
            "transaction": {"undo_chunk": label, "sources_unchanged": not consumes,
                            "selection_restored": True, "source_deletion": consumes}}


def reduce_entries(entries, character="", manage_undo=True, prepared_cache=None, weight_options=None):
    """Reduce meshes; private cache is valid only until this transaction's combine.

    The synchronous caller owns these derived meshes and must not edit them between
    reduction and combine. Public API calls never accept or reuse this cache.
    """
    cmds, _om, _oma = geo.modules()
    _cache_option(prepared_cache, manage_undo)
    if not isinstance(entries, list) or not entries:
        raise ValueError("entries must be a non-empty list")
    options = [validate_entry(entry) for entry in entries]
    _names_available([entry["output_name"] for entry in entries])
    prepared = [_preflight(entry.get("source"), entry.get("expected_uuid"), transfer_skin=preserve is not False)
                for entry, (_ratio, preserve) in zip(entries, options)]
    if len({row["source"] for row in prepared}) != len(prepared):
        raise ValueError("Duplicate sources in a batch are not allowed")
    for row, (_ratio, preserve) in zip(prepared, options):
        if preserve is True and not row["skin"]:
            raise ValueError("preserve_skin=true requires a source skinCluster")
    pending_cache = {}
    weight_reports = []
    stage_times = {"source_weights": 0., "geometry": 0., "transfer_repair": 0.}

    def mutate():
        rows = []
        for entry, row, (ratio, preserve) in zip(entries, prepared, options):
            from time import perf_counter
            row_started = perf_counter()
            previous_times = dict(stage_times)
            working = None
            weight_source = None
            context = row["skin"]
            planned = None
            if weight_options and context:
                from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import dair_engine as engine
                started = perf_counter()
                planned = engine._prepare(context["skin"], weight_options, *geo.modules(), engine.ng_engine.MAX_WEIGHT_VALUES)
                weight_reports.append(engine._summarize(planned))
                stage_times["source_weights"] += perf_counter() - started
            started = perf_counter()
            if planned is not None and ratio < 1:
                weight_source = _copy(row["source"], entry["output_name"] + "_weightSource")
                source_skin = skin.bind_copy(context, weight_source, planned)
                context = {**context, "skin": source_skin}
            if ratio < 1:
                working = _copy(weight_source or row["source"], entry["output_name"] + "_construction")
                cmds.polyReduce(working, percentage=100 * (1 - ratio), **REDUCE)
            expected = geo.snapshot(working) if working else row["snapshot"]
            output = _copy(working or row["source"], entry["output_name"])
            target_skin = skin.bind_copy(context, output, planned if ratio == 1 else None) if context and preserve is not False else ""
            stage_times["geometry"] += perf_counter() - started
            if target_skin and weight_options and ratio < 1:
                started = perf_counter()
                repair = engine.run({**weight_options, "remove_unused_influences": False,
                                     "skin_clusters": [target_skin]}, manage_undo=False)
                stage_times["transfer_repair"] += perf_counter() - started
                weight_reports.extend(repair["skins"])
            if target_skin and weight_options:
                cmds.setAttr(target_skin + ".maxInfluences", weight_options["max_influences"])
                cmds.setAttr(target_skin + ".maintainMaxInfluences", True)
            geo.copy_visibility(row["snapshot"]["visibility"], output)
            actual = geo.snapshot(output, target_skin)
            checks = geo.verify_clean(expected, actual)
            target_context = None
            if target_skin:
                checks["valid_skin_weights"] = True
                if ratio == 1 and planned is None:
                    checks.update(skin.verify_weights(row["snapshot"], actual))
                    if not checks["weights"] or not checks["blend_weights"]:
                        raise RuntimeError("Unreduced copy changed skin weights")
                target_context = skin.binding_context(target_skin, actual["shape"])
                checks["binding_context"] = skin.bindings_equal(target_context["bindings"], row["skin"]["bindings"])
                if not checks["binding_context"]:
                    raise RuntimeError("Copied skin binding context differs")
            checks["visibility"] = actual["visibility"] == row["snapshot"]["visibility"]
            if not checks["visibility"]:
                raise RuntimeError("Output visibility signature differs")
            if working:
                cmds.delete(working)
            if weight_source:
                cmds.delete(weight_source)
            if prepared_cache is not None:
                guard = _cache_guard(output, actual["uuid"], context=target_context)
                pending_cache[actual["uuid"]] = {"guard": guard, "prepared": {
                    "source": output, "shape": actual["shape"], "skin": guard["skin"],
                    "snapshot": actual, "parent": guard["parent"]}}
            rows.append(_result_row(row, output, actual, checks, keep_face_ratio=ratio,
                                   actual_face_ratio=actual["counts"]["faces"] / row["snapshot"]["counts"]["faces"],
                                   lossy=ratio < 1, comparison_basis="reduced_construction_to_clean_output"))
            rows[-1]["timing_seconds"] = {key: stage_times[key] - previous_times[key] for key in stage_times}
            rows[-1]["timing_seconds"]["total"] = perf_counter() - row_started
        return rows
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights.dair_solver import numeric_session
    with numeric_session():
        result = _transaction("maya.asset.mesh_reduce", prepared, mutate, character, manage_undo)
    result["weights"] = weight_reports
    result["timing_seconds"] = stage_times
    if prepared_cache is not None:
        prepared_cache.update(pending_cache)
    return result


def combine_groups(groups, character="", manage_undo=True, prepared_cache=None):
    """Combine compatible siblings; only explicit consume_inputs allows source removal."""
    cmds, _om, _oma = geo.modules()
    _cache_option(prepared_cache, manage_undo)
    if not isinstance(groups, list) or not groups:
        raise ValueError("groups must be a non-empty list")
    _names_available([_name(group.get("output_name")) for group in groups])
    prepared_groups, all_prepared = [], []
    for group in groups:
        consume = group.get("consume_inputs", False)
        if not isinstance(consume, bool):
            raise ValueError("consume_inputs must be a boolean")
        sources, uuids = group.get("sources"), group.get("expected_uuids")
        if not isinstance(sources, list) or len(sources) < 2 or not isinstance(uuids, list) or len(sources) != len(uuids):
            raise ValueError("Each combine group requires at least two sources and matching expected_uuids")
        rows = [_combine_preflight(source, identity, prepared_cache) for source, identity in zip(sources, uuids)]
        for row in rows:
            row["consume"] = consume
        if len({row["parent"] for row in rows}) != 1:
            raise ValueError("Combine sources must share a parent to preserve ancestor controls")
        if any(row["snapshot"]["visibility"] != rows[0]["snapshot"]["visibility"] for row in rows):
            raise ValueError("Combine sources have different local visibility/display drivers")
        skin.compatible([row["skin"] for row in rows])
        expected = geo.concatenate([row["snapshot"] for row in rows])
        prepared_groups.append((rows, expected))
        all_prepared.extend(rows)
    if len({row["source"] for row in all_prepared}) != len(all_prepared):
        raise ValueError("Combine groups must have distinct sources")

    def mutate():
        results = []
        for group, (rows, expected) in zip(groups, prepared_groups):
            copies = []
            if group.get("consume_inputs", False):
                copies = list(group["sources"])
            else:
                for index, row in enumerate(rows):
                    node = _copy(row["source"], group["output_name"] + f"_input_{index:04d}")
                    if row["skin"]:
                        skin.bind_copy(row["skin"], node)
                    copies.append(node)
            command = cmds.polyUniteSkinned if rows[0]["skin"] else cmds.polyUnite
            returned = command(copies, constructionHistory=False, mergeUVSets=1) or []
            outputs = [node for node in returned if cmds.objExists(node) and cmds.nodeType(node) == "transform"]
            if len(outputs) != 1:
                raise RuntimeError("Native combine did not return exactly one output transform")
            output = cmds.rename(outputs[0], group["output_name"])
            parent = rows[0]["parent"]
            current_parent = (cmds.listRelatives(output, parent=True, fullPath=True) or [None])[0]
            if current_parent != parent:
                output = cmds.parent(output, parent, absolute=True)[0] if parent else cmds.parent(output, world=True)[0]
            output = cmds.ls(output, long=True)[0]
            target_skin = geo.protect_dependencies(output, _shape(output))
            if target_skin:
                bindings = {key: value for row in rows for key, value in row["skin"]["bindings"].items()}
                skin.restore_binding_context(target_skin, {**rows[0]["skin"], "bindings": bindings})
            geo.copy_visibility(rows[0]["snapshot"]["visibility"], output)
            actual = geo.snapshot(output, target_skin)
            checks = geo.verify_clean(expected, actual, check_skin=bool(target_skin))
            if target_skin:
                context = skin.binding_context(target_skin, actual["shape"])
                bindings = {key: value for row in rows for key, value in row["skin"]["bindings"].items()}
                checks["binding_context"] = (skin.bindings_equal(context["bindings"], bindings)
                    and context["settings"] == rows[0]["skin"]["settings"]
                    and skin.matrix_equal(context["geom_matrix"], rows[0]["skin"]["geom_matrix"]))
                if not checks["binding_context"]:
                    settings_diff = {key: (value, context["settings"].get(key))
                                     for key, value in rows[0]["skin"]["settings"].items()
                                     if value != context["settings"].get(key)}
                    binding_diff = [key for key in set(bindings) | set(context["bindings"])
                                    if key not in bindings or key not in context["bindings"] or
                                    not skin.bindings_equal({key: bindings[key]}, {key: context["bindings"][key]})]
                    detail = {key: {"expected": bindings.get(key), "actual": context["bindings"].get(key)}
                              for key in sorted(binding_diff)[:1]}
                    raise RuntimeError("Native combine changed binding context for {}: settings={}, bindings={}, sample={}, geom_matrix={}".format(
                        group["output_name"], settings_diff, binding_diff, detail,
                        (rows[0]["skin"]["geom_matrix"], context["geom_matrix"])))
            checks["visibility"] = actual["visibility"] == rows[0]["snapshot"]["visibility"]
            if not checks["visibility"]:
                raise RuntimeError("Combined visibility signature differs")
            cmds.delete([node for node in copies if cmds.objExists(node)])
            results.append(_result_row(rows[0], output, actual, checks,
                sources=group["sources"], source_uuids=group["expected_uuids"],
                inputs=[geo.summary(row["snapshot"]) for row in rows],
                consume_inputs=group.get("consume_inputs", False),
                comparison_basis="ordered_inputs_to_combined_output"))
        return results
    result = _transaction("maya.asset.mesh_combine", all_prepared, mutate, character, manage_undo)
    if prepared_cache is not None:
        for row in all_prepared:
            prepared_cache.pop(row["snapshot"]["uuid"], None)
    return result
