"""Deterministic Maya Proxy construction; AI decisions enter only through a plan."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

from . import inventory, material_cleanup, membership, outside, plan as plans, safety


TIMING_SCOPE = {
    "phases": "Exclusive wall-clock stages; unmeasured orchestration overhead can remain.",
    "totals": "Parent envelopes, not additive phases: apply_total covers transaction setup through save; api_before_report/api_total cover the API before/after build report writing.",
    "build_report": "Through save and report preparation; report_write and api_total are in the public output only.",
}


@contextmanager
def _timed(timings, stage):
    started = perf_counter()
    try:
        yield
    finally:
        timings[stage] = perf_counter() - started


def _timing_output(output, timings, totals, started):
    totals["api_total"] = perf_counter() - started
    return {**output, "timing_seconds": timings, "timing_totals_seconds": totals,
            "timing_scope": TIMING_SCOPE}


def load_plan(params):
    value, filename = params.get("plan"), params.get("plan_path")
    if value and filename:
        raise ValueError("Supply either plan or plan_path, not both")
    if filename:
        value = json.loads(safety.managed(filename, suffix={".json"}).read_text(encoding="utf-8"))
    return value or {}


def _summary(snapshot, artifact):
    return {key: snapshot[key] for key in ("policy_version", "scene_path", "scene_fingerprint",
            "mesh_count", "faces", "categories", "fingerprint_scope", "policy",
            "cache_root", "cache_candidates", "outside_cache_summary")} | {
                "inventory_path": artifact}


def _weight_engine(options):
    if options["method"] == "dair_inspired":
        from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import dair_engine
        dair_engine.require_deps()
        return dair_engine
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import ng_engine
    return ng_engine


def _weight_params(options, skins, mode):
    return {key: value for key, value in options.items() if key not in
            ("method", "additional_skin_uuids")} | {"skin_clusters": skins, "mode": mode}


def preflight(cmds, normalized):
    outside.preflight_discard(cmds, normalized["discard"], list(normalized["discard_by_id"].values()))
    material_cleanup.preflight(normalized["cleanup_unused_materials"])
    rows = [normalized["by_id"][source] for source in normalized["changed"]]
    mapped_ids = {r['source_uuid'] for r in normalized.get('region_entries', [])}
    safety.check_consumers(cmds, [normalized['by_id'][uid] for uid in normalized['changed'] if uid not in mapped_ids])
    if mapped_ids and not normalized['retain_sources']:
        raise ValueError('Mapped outputs require source preservation for downstream consumers')
    safety.preflight_names(cmds, normalized)
    options = normalized["weights"]
    if options["method"] != "dair_inspired":
        raise ValueError("The Proxy transaction currently requires dair_inspired; ng_proportional is a standalone API")
    explicit = options.get("additional_skin_uuids", [])
    if not isinstance(explicit, list) or any(not isinstance(x, str) for x in explicit):
        raise ValueError("additional_skin_uuids must be an explicit list of skinCluster UUIDs")
    known = {row["skin"]["uuid"] for row in normalized["by_id"].values() if row["skin"]}
    if set(explicit) - known:
        raise ValueError("additional_skin_uuids refers to a skin outside the inspected outputs")
    skins = {row["skin"]["name"] for row in rows if row["skin"]}
    skins.update(safety.resolve(cmds, source) for source in explicit)
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import dair_engine
    # The standalone validator requires at least one target name. With no skins,
    # this sentinel validates options only: it is never resolved or executed.
    parameter_targets = sorted(skins) or ["__proxy_parameter_validation__"]
    dair_engine.validate_params(_weight_params(options, parameter_targets, "inspect"))
    weight_sources = {"skins": [], "weight_validation": "not_applicable"}
    if skins:
        engine = _weight_engine(options)
        # Source compatibility is required before mutation. Full weight arrays
        # and local solve preparation belong to source-first construction; the
        # final skin receives retained rows and only needs legality verification.
        sizes = {row["skin"]["name"]: row["vertices"] * len(row["skin"]["influences"])
                 for row in normalized["by_id"].values() if row["skin"]}
        from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights.ng_engine import MAX_WEIGHT_VALUES
        batches, batch, size = [], [], 0
        for name in sorted(skins):
            if batch and size + sizes[name] > MAX_WEIGHT_VALUES:
                batches.append(batch)
                batch, size = [], 0
            batch.append(name)
            size += sizes[name]
        if batch:
            batches.append(batch)
        checked = [engine.preflight_sources(batch) for batch in batches]
        weight_sources = {"skins": [row for result in checked for row in result["skins"]],
                          "batch_count": len(checked),
                          "weight_validation": "geometry inheritance and serial final skin verification"}
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("Maya undo must be enabled for the Proxy construction transaction")
    return {"weight_sources": weight_sources, "undo_enabled": True}


def _reduce(cmds, normalized, *, prepared_cache=None):
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.construction import construct_groups
    ratios = {r['source_uuid']: r['keep_face_ratio'] for r in normalized['reductions']}
    merged_ids = {uid for group in normalized['merges'] for uid in group['sources']}
    groups = []
    for i, group in enumerate(normalized['merges']):
        groups.append({'output_name': normalized['character']+'_proxyMerge_'+str(i),
                       'source_ids': group['sources'],
                       **({'search':normalized.get('geometry_search',{}).get(group.get('output_name'))} if group.get('output_name') in normalized.get('geometry_search',{}) else {})})
    for entry in normalized['reductions']:
        if entry['source_uuid'] not in merged_ids:
            groups.append({'output_name': normalized['character']+'_proxyBuild_'+entry['source_uuid'].replace('-','')[:12],
                           'source_ids': [entry['source_uuid']],
                           **({'search':normalized.get('geometry_search',{}).get(entry.get('output_name'))} if entry.get('output_name') in normalized.get('geometry_search',{}) else {})})
    if not groups:
        normalized['constructed_merges'] = []
        return []
    for group in groups:
        group['entries'] = [{'source': normalized['by_id'][uid]['path'], 'expected_uuid': uid,
                             'keep_face_ratio': ratios.get(uid, 1.)} for uid in group['source_ids']]
    engine = _weight_engine(normalized['weights'])
    options = engine.validate_params(_weight_params(normalized['weights'], ['__source_planning__'], 'apply'))
    result = construct_groups(groups, options, character=normalized['character'], manage_undo=False)
    normalized['source_weight_results'] = result['weights']
    normalized['source_processing_times'] = result['timing_seconds']
    normalized['construction_stats'] = {key: result[key] for key in ('skin_nodes_created', 'weight_write_calls')}
    normalized['constructed_merges'] = result['groups'][:len(normalized['merges'])]
    normalized['geometry_search_results'] = [{**row,'sources':group['source_ids']} for group,row in zip(groups,result['groups']) if 'search' in row]
    return result['rows']


def _combine(cmds, normalized, reductions, *, prepared_cache=None):
    # Geometry and array assembly already produced one skin per final group.
    return normalized.pop('constructed_merges')


def _outputs(cmds, normalized, reduced, merged):
    source_map = {row["source_uuid"]: row for row in reduced}
    merged_sources = {source for group in normalized["merges"] for source in group["sources"]}
    outputs = []
    for entry in normalized["reductions"]:
        if entry["source_uuid"] not in merged_sources:
            outputs.append({"uuid": source_map[entry["source_uuid"]]["result_uuid"],
                            "sources": [entry["source_uuid"]], "name": entry["output_name"]})
    for group, row in zip(normalized["merges"], merged):
        outputs.append({"uuid": row["result_uuid"], "sources": group["sources"],
                        "name": group["output_name"]})
    for row in normalized.get('regional_outputs', []):
        outputs.append({'uuid': row['result_uuid'], 'sources': row.get('source_uuids',[row['source_uuid']]),
                        'name': row['output_name'], 'mapped': row})
    for output in outputs:
        target = safety.resolve(cmds, output["uuid"])
        source = normalized["by_id"][output["sources"][0]]
        parent = (cmds.listRelatives(target, parent=True, fullPath=True) or [""])[0]
        if parent != source["parent"]:
            target = cmds.parent(target, source["parent"])[0] if source["parent"] else cmds.parent(target, world=True)[0]
        safety.copy_display(cmds, source["path"], target)
        shapes = cmds.listRelatives(target, shapes=True, noIntermediate=True, fullPath=True, type="mesh") or []
        safety.copy_display(cmds, source["shape"], shapes[0])
        # Proxy polygon budgets describe the base mesh; inherited smooth preview
        # would add viewport subdivision after the geometry optimization.
        cmds.setAttr(shapes[0]+'.displaySmoothMesh',0)
        membership.transfer(cmds, source, target)
        if output.get('mapped') and len(output['sources'])==1:
            membership.transfer_vertices(cmds, source, target, output['mapped']['source_vertex_ids'])
        output["check"] = safety.check_result(cmds, safety.resolve(cmds, output["uuid"]),
                             [normalized["by_id"][uid] for uid in output["sources"]],
                             merged=len(output["sources"]) > 1, mapped=output.get('mapped'))
    return outputs


def _prune(cmds, normalized, outputs):
    skins = {row["check"]["skin_cluster"] for row in outputs if row["check"]["skin_cluster"]}
    additional = {safety.resolve(cmds, uid) for uid in normalized["weights"].get("additional_skin_uuids", [])}
    if not skins and not additional:
        return {"skins": [], "changed": False}
    engine = _weight_engine(normalized["weights"])
    params = _weight_params(normalized["weights"], sorted(skins), "apply")
    if normalized["weights"]["method"] == "dair_inspired":
        # Each final mesh is an independent solve; keep peak dense storage bounded.
        batches = [engine.finalize({**params, "skin_clusters": [name]}) for name in sorted(skins)]
        batches += [engine.run({**params, "skin_clusters": [name]}, manage_undo=False) for name in sorted(additional)]
        result = dict(batches[0])
        result["skins"] = [row for batch in batches for row in batch["skins"]]
        result["timing_seconds"] = {key: sum(batch["timing_seconds"].get(key, 0) for batch in batches)
                                    for key in result["timing_seconds"]}
        result["serial_skin_batches"] = len(batches)
        return result
    raise ValueError("Unsupported transactional weight engine")


def _replace(cmds, normalized, outputs, intermediates):
    final_ids = {row["uuid"] for row in outputs}
    original_ids = normalized["changed"]
    backups, parents = [], {}
    for source in original_ids:
        node = safety.resolve(cmds, source)
        if normalized.get("retain_sources", False):
            parent = normalized["by_id"][source]["parent"]
            if parent not in parents:
                group = cmds.createNode("transform", name="cgi_proxy_originals", parent=parent) if parent else cmds.createNode("transform", name="cgi_proxy_originals")
                cmds.setAttr(group + ".visibility", False)
                parents[parent] = cmds.ls(group, long=True)[0]
            # Identity local parent preserves the original transform and skin.
            cmds.parent(node, parents[parent], relative=True)
            backups.append({"uuid": source, "original_path": normalized["by_id"][source]["path"],
                            "parent": parent, "path": safety.resolve(cmds, source)})
        else:
            cmds.delete(node)
    normalized["retained_sources"] = backups
    for row in intermediates:
        if row["result_uuid"] not in final_ids and cmds.ls(row["result_uuid"], long=True):
            cmds.delete(safety.resolve(cmds, row["result_uuid"]))
    checks = []
    for output in outputs:
        target = cmds.rename(safety.resolve(cmds, output["uuid"]), output["name"])
        checks.append(safety.check_result(cmds, safety.resolve(cmds, output["uuid"]),
                       [normalized["by_id"][uid] for uid in output["sources"]],
                       merged=len(output["sources"]) > 1, mapped=output.get('mapped')))
        if target.rsplit("|", 1)[-1] != output["name"]:
            raise RuntimeError("Maya changed the explicit output name")
    return checks


def apply(cmds, payload, normalized, snapshot, plan, destination, *,
          timing_seconds=None, timing_totals_seconds=None):
    apply_started = perf_counter()
    timings = timing_seconds if timing_seconds is not None else {}
    totals = timing_totals_seconds if timing_totals_seconds is not None else {}
    # Only verified derivatives from this one serial transaction may be reused.
    # No source snapshot from a previous API call or plan enters this cache.
    prepared_cache = {}
    with _timed(timings, "transaction_setup"):
        selection = cmds.ls(selection=True, long=True) or []
        before_ids = set(cmds.ls(uuid=True) or [])
    old_name = snapshot["scene_path"]
    marker = None
    chunk_name = "ProxyRigConstruction"
    cmds.undoInfo(openChunk=True, chunkName=chunk_name)
    try:
        marker = cmds.createNode("network", name="cgi_proxy_transaction")
        with _timed(timings, 'regional'):
            normalized['regional_outputs'] = []
            normalized['regional_weight_seconds'] = 0.
            from cgi_pipeline.capabilities.maya.asset.mesh_reduce import mapped
            from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights.dair_solver import numeric_session
            options = None
            if normalized.get('region_entries'):
                engine = _weight_engine(normalized['weights'])
                options = engine.validate_params(_weight_params(normalized['weights'], ['__regional__'], 'apply'))
            with numeric_session():
                prepared_entries=[]
                for entry in normalized.get('region_entries', []):
                    uid = entry['source_uuid']
                    source = normalized['by_id'][uid]
                    prepared = mapped.prepare(source['path'], uid, entry['regions'], options)
                    normalized['regional_weight_seconds'] += prepared['weight_seconds']
                    prepared_entries.append((prepared,entry['regions'],entry.get('quality')))
                if prepared_entries:
                    result=mapped.apply_batch(prepared_entries,normalized['character']+'_regionBuild')
                    normalized['regional_weight_results']=result['weight_report']
                    for row in result['outputs']:
                        faces={uid:normalized['by_id'][uid]['faces'] for uid in row['source_uuids']}
                        row.update(before_faces=sum(faces.values()),source_before_faces=faces,quality=result['quality'])
                    normalized['regional_outputs'].extend(result['outputs'])
        with _timed(timings, "reduce"):
            reduced = _reduce(cmds, normalized, prepared_cache=prepared_cache)
        with _timed(timings, "combine"):
            merged = _combine(cmds, normalized, reduced, prepared_cache=prepared_cache)
        with _timed(timings, "outputs"):
            outputs = _outputs(cmds, normalized, reduced, merged)
        with _timed(timings, "weights"):
            weights = _prune(cmds, normalized, outputs)
        with _timed(timings, "replace"):
            checks = _replace(cmds, normalized, outputs, reduced + merged)
        with _timed(timings, "discard"):
            discarded = []
            for entry in normalized["discard"]:
                source = normalized["discard_by_id"][entry["uuid"]]
                cmds.delete(safety.resolve(cmds, entry["uuid"]))
                discarded.append({**entry, "path": source["path"]})
        with _timed(timings, "material_cleanup"):
            cleanup = material_cleanup.run(cmds, normalized["cleanup_unused_materials"])
        cmds.delete(marker)
        with _timed(timings, "inventory_final"):
            backup_ids = {r["uuid"] for r in normalized.get("retained_sources", [])}
            backup_paths = [r['path'] for r in normalized.get('retained_sources', [])]
            if backup_ids and set(cmds.ls(backup_paths, uuid=True) or []) != backup_ids:
                raise RuntimeError('Retained source identity changed')
            final = inventory.inspect(cmds, project=snapshot.get("project", ""), cache_group=snapshot.get("cache_root", ""),
                                      exclude_transforms=backup_paths)
        with _timed(timings, "retained_validation"):
            protected = normalized["keep"] + normalized["protect"]
            for retained in protected:
                old = normalized["by_id"][retained["uuid"]]
                current = next(row for row in final["meshes"] if row["uuid"] == retained["uuid"])
                if any(current[key] != old[key] for key in ("shape_uuid", "faces", "vertices", "uv_sets", "materials", "display", "memberships")):
                    raise RuntimeError("A kept/protected output changed: " + old["path"])
        live_selection = [node for node in selection if cmds.objExists(node)]
        cmds.select(live_selection, replace=True) if live_selection else cmds.select(clear=True)
    except Exception as exc:
        cmds.undoInfo(closeChunk=True)
        if marker is not None:
            if cmds.undoInfo(query=True, undoName=True) != chunk_name:
                raise RuntimeError("Proxy rollback refused: matching native undo chunk is missing; original error: " + str(exc)) from exc
            cmds.undo()
        recovered = set(cmds.ls(uuid=True) or []) == before_ids
        structural = inventory.inspect(cmds, project=snapshot.get("project", ""), cache_group=snapshot.get("cache_root", ""))["scene_fingerprint"] == snapshot["scene_fingerprint"]
        raise RuntimeError(f"Proxy construction failed; native undo restored node identities={recovered}, structural snapshot={structural}: {exc}") from exc
    else:
        cmds.undoInfo(closeChunk=True)
    with _timed(timings, "report_prepare"):
        report = {"policy_version": inventory.POLICY_VERSION, "character": normalized["character"],
              "source_master": plan.get("source_master", {}), "source_scene": old_name,
              "source_fingerprint": snapshot["scene_fingerprint"], "plan": plan,
              "before": {"meshes": snapshot["mesh_count"], "faces": snapshot["faces"]},
              "after": {"meshes": final["mesh_count"], "faces": final["faces"]},
              "outputs": checks, "weights": weights, "protected": normalized["protect"],
              "retained_sources": normalized.get("retained_sources", []),
              "source_weight_results": normalized.get("source_weight_results", []),
              "source_processing_times": normalized.get("source_processing_times", {}),
              "construction_stats": normalized.get("construction_stats", {}),
              "geometry_search_results": normalized.get("geometry_search_results", []),
              "geometry_rows": reduced,
              "regional_outputs": normalized.get('regional_outputs', []),
              "regional_weight_seconds": normalized.get('regional_weight_seconds', 0.),
              "regional_weight_results":normalized.get('regional_weight_results',[]),
              "preflight": normalized.get("preflight", {}),
              "cache_root": snapshot["cache_root"], "discarded": discarded, "material_cleanup": cleanup,
              "outside_cache_before": snapshot["outside_cache_summary"], "outside_cache_after": final["outside_cache_summary"],
              "kept": normalized["keep"], "full_scene_coverage": normalized["full_scene_coverage"],
              "unscoped_meshes": normalized["unscoped_meshes"], "changed_sources": len(normalized["changed"]),
              "protected_check_scope": ["shape UUID", "face/vertex counts", "UV-set and material identities", "display", "memberships"],
              "limitations": ["Visual acceptance and animation/performance acceptance are separate from structural checks.",
                              "Face/mesh totals and full_scene_coverage describe configured cache outputs, not outside-cache helpers.",
                              "Kept/protected skins are unchanged unless additional_skin_uuids explicitly includes them."],
              "timing_seconds": timings, "timing_totals_seconds": totals,
              "timing_scope": TIMING_SCOPE}
    from cgi_pipeline.capabilities.maya.asset.save_scene.save_scene import execute as save
    with _timed(timings, "save"):
        receipt = save({"parameters": {"save_path": str(destination)}})
    if receipt["status"] != "SUCCESS":
        if cmds.undoInfo(query=True, undoName=True) != chunk_name:
            raise RuntimeError("Proxy save failed and native rollback entry is unavailable; retain the saved sandbox checkpoint: " + receipt.get("error", ""))
        cmds.undo()
        cmds.file(rename=old_name)
        raise RuntimeError("Proxy save failed; geometry transaction undone: " + receipt.get("error", ""))
    report["output_path"] = receipt["output"]["output_path"]
    totals["apply_total"] = perf_counter() - apply_started
    return report


def run(payload):
    import maya.cmds as cmds
    api_started = perf_counter()
    timings, totals = {}, {}
    params = dict(payload.get("parameters") or {})
    mode = params.get("mode", "inspect")
    if mode == 'prepare':
        from .scene_prep import run as prepare
        return prepare(payload)
    if mode in ('finalize', 'finalize_preview'):
        from .finalize import run as finalize
        return finalize(payload)
    if mode not in ("inspect", "preview", "apply"):
        raise ValueError("mode must be inspect, preview or apply")
    with _timed(timings, "inventory_initial"):
        snapshot = inventory.inspect(cmds, project=payload.get("project", ""))
    root = safety.artifact_root(payload, snapshot["scene_path"])
    with _timed(timings, "inventory_report"):
        artifact = safety.write_report(root, "inventory", snapshot)
    summary = _summary(snapshot, artifact)
    if mode == "inspect":
        return "SUCCESS", _timing_output(summary, timings, totals, api_started)
    plan = load_plan(params)
    if not plan:
        return "NEEDS_ATTENTION", _timing_output({**summary, "reason": "Inspect representatives, then supply an explicit class/exception plan"}, timings, totals, api_started)
    with _timed(timings, "plan_validation"):
        normalized = plans.validate(snapshot, plan, params.get("character", ""))
    if payload.get("run_dir"):
        normalized["weights"]["solver_workdir"] = str(safety.managed(Path(payload["run_dir"]) / "work" / "skin_solver"))
    safety.managed(snapshot["scene_path"], suffix={".ma", ".mb"})
    destination = safety.output_path(params.get("output_path"), normalized["character"], snapshot["scene_path"])
    if not plan.get("source_master", {}).get("path") or not plan.get("source_master", {}).get("version"):
        raise ValueError("source_master path and version provenance are required")
    with _timed(timings, "preflight"):
        normalized["preflight"] = preflight(cmds, normalized)
    summary.update({"plan_valid": True, "full_scene_coverage": normalized["full_scene_coverage"],
                    "unscoped_meshes": normalized["unscoped_meshes"], "planned_output": str(destination),
                    "reduce_count": len(normalized["reductions"]), "merge_count": len(normalized["merges"]),
                    "protected_count": len(normalized["protect"]), "kept_count": len(normalized["keep"])})
    summary.update({"discard_count": len(normalized["discard"]),
                    "cleanup_unused_materials": normalized["cleanup_unused_materials"]})
    if mode == "preview":
        return "SUCCESS", _timing_output(summary, timings, totals, api_started)
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights.dair_solver import numeric_session
    with numeric_session():
        report = apply(cmds, payload, normalized, snapshot, plan, destination,
                       timing_seconds=timings, timing_totals_seconds=totals)
    totals["api_before_report"] = perf_counter() - api_started
    with _timed(timings, "report_write"):
        report_path = safety.write_report(root, "build", report)
    return ("SUCCESS" if normalized["full_scene_coverage"] else "PARTIAL"), _timing_output({
        **summary, "output_path": report["output_path"], "report_path": report_path,
        "before": report["before"], "after": report["after"], "outputs": report["outputs"],
        "discarded_count": len(report["discarded"]),
        "material_cleanup": {key: value for key, value in report["material_cleanup"].items() if key != "deleted"},
        "changed_sources": report["changed_sources"], "acceptance": "structural_only"}, timings, totals, api_started)
