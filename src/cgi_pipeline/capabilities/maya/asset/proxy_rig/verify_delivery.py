"""One post-reopen structural/weight pass; no repeat solve or geometry rebuild."""
from __future__ import annotations
import json
import math
from pathlib import Path


def display_matches(actual, expected):
    """Compare authored mechanisms; a driven value may vary with control state."""
    if isinstance(expected, dict):
        if set(actual) != set(expected): return False
        if 'inputs' in expected and expected['inputs']:
            return actual['inputs'] == expected['inputs']
        return all(display_matches(actual[k], value) for k, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(display_matches(a, b) for a, b in zip(actual, expected))
    return actual == expected


def target_driver_matches(actual, expected):
    if actual['driver'] != expected['driver'] or actual['alias'] != expected['alias']:
        return False
    return bool(expected['driver']) or math.isclose(actual['value'], expected['value'], rel_tol=1e-8, abs_tol=1e-8)


def _retained_paths(cmds, rows):
    ids = {r['uuid'] for r in rows}
    paths = (cmds.ls(list(ids), long=True) or []) if ids else []
    # Maya's UUID-input form can return names even with uuid=True. Resolve DAG
    # paths first; query UUIDs on these explicit names, not on UUID inputs.
    present = set(cmds.ls(paths, uuid=True) or []) if paths else set()
    if ids - present:
        raise RuntimeError('Candidate original meshes are missing')
    return paths


def verify(build_path, source_inventory_path, output_path):
    import numpy as np
    import maya.cmds as cmds
    from maya.api import OpenMaya as om, OpenMayaAnim as oma
    from . import inventory, safety
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import ng_engine
    build = json.loads(safety.managed(build_path).read_text(encoding="utf-8"))
    source = json.loads(safety.managed(source_inventory_path).read_text(encoding="utf-8"))
    current_file = (cmds.file(query=True, sceneName=True) or "").replace("\\", "/")
    if Path(current_file).resolve() != Path(build["output_path"]).resolve():
        raise ValueError("Reopen the actual saved Proxy before delivery verification")
    backup_paths = _retained_paths(cmds, build.get('retained_sources', []) + build.get('binding_inputs', []))
    final = inventory.inspect(cmds, project=source["project"], cache_group=source["cache_root"],exclude_transforms=backup_paths)
    old = {r["uuid"]: r for r in source["meshes"]}
    now = {r["uuid"]: r for r in final["meshes"]}
    checks, problems = [], []
    from .report import output_sources
    planned_sources=output_sources(build['plan'])
    regional = {r['result_uuid']: r for r in build.get('regional_outputs', [])}
    for result in build["outputs"]:
        uid = result["result_uuid"]
        row = now.get(uid)
        if not row or row["faces"] != result["faces"]:
            problems.append("Missing or changed generated output: " + result["result"])
            continue
        if any(cmds.nodeType(n) in {"polyReduce", "polyUnite", "polyUniteSkinned"}
               for n in (cmds.listHistory(row["shape"], pruneDagObjects=True) or [])):
            problems.append("Construction history on " + row["path"])
        sources = [old[s] for s in planned_sources[row["path"].split("|")[-1]]]
        compare_keys = ('display', 'shape_display') if uid in regional else ('display','shape_display','memberships')
        if any(not display_matches(row[key], sources[0][key]) for key in compare_keys):
            problems.append("Generated display/membership differs: " + row["path"])
        if set(row["uv_sets"]) != set().union(*(set(s["uv_sets"]) for s in sources)):
            problems.append("Generated UV set lost: " + row["path"])
    for entry in build["kept"] + build["protected"]:
        uid = entry["uuid"]
        if uid not in now or any(not display_matches(now[uid][key], old[uid][key]) for key in
                ("shape_uuid", "faces", "uv_sets", "materials", "display", "shape_display", "memberships")):
            problems.append("Retained output structure differs: " + old[uid]["path"])
    missing_material_faces = 0
    for row in final["meshes"]:
        selection = om.MSelectionList(); selection.add(row["shape"])
        dag = selection.getDagPath(0); fn = om.MFnMesh(dag)
        shaders, indices = fn.getConnectedShaders(dag.instanceNumber())
        missing_material_faces += sum(index < 0 or index >= len(shaders) for index in indices)
    if missing_material_faces:
        problems.append("Faces without valid material assignment")
    cap, tau = build["weights"]["max_influences"], build["weights"]["threshold"]
    maximum, min_positive, sum_error, zero_relations, invalid = 0, 1.0, 0.0, 0, 0
    skins = sorted({r["skin"]["name"] for r in final["meshes"] if r["skin"]})
    for name in skins:
        snapshot = ng_engine._capture(name, cmds, om, oma)
        weights = np.asarray(snapshot["weights"]).reshape((-1, snapshot["width"]))
        positive = weights > 0
        sum_error = max(sum_error, float(np.abs(weights.sum(axis=1) - 1).max()))
        maximum = max(maximum, int(np.count_nonzero(positive, axis=1).max()))
        invalid += int(np.count_nonzero(~np.isfinite(weights) | (weights < 0)))
        if positive.any():
            min_positive = min(min_positive, float(weights[positive].min()))
        zero_relations += int(np.count_nonzero(~positive.any(axis=0)))
    if maximum > cap or min_positive <= tau or sum_error > 1e-7 or invalid or zero_relations:
        problems.append("Final actual weight constraints failed")
    if {"meshes": final["mesh_count"], "faces": final["faces"]} != build["after"]:
        problems.append("Saved scene totals differ from build")
    if cmds.objExists("|Group|Geometry|data"):
        problems.append("Master-only data still present")
    for row in source["outside_cache"]:
        if row["status"] == "protected" and not cmds.ls(row["uuid"], long=True):
            problems.append("Protected auxiliary missing: " + row["path"])
    regional_checks = verify_regions(build, old, now, cmds, om)
    problems.extend(regional_checks['problems'])
    result = {"passed": not problems, "problems": problems, "scene": current_file,
              "cache_models": final["mesh_count"], "cache_faces": final["faces"],
              "generated_outputs_checked": len(build["outputs"]),
              "retained_outputs_checked": len(build["kept"]) + len(build["protected"]),
              "retained_outputs": [{"path": old[e["uuid"]]["path"],
                                    "faces": old[e["uuid"]]["faces"],
                                    "reason": e["reason"]}
                                   for e in build["kept"] + build["protected"]],
              "missing_material_faces": missing_material_faces,
              "skin_clusters_checked": len(skins), "actual_max_influences": maximum,
              "actual_min_positive_weight": min_positive, "maximum_sum_error": sum_error,
              "invalid_weights": invalid, "unused_skin_relations": zero_relations,
              "data_visData_absent": not cmds.objExists("|Group|Geometry|data"),
              "regional": regional_checks,
              "visual_review": "Recorded separately from structural verification",
              "animation_performance": "Not tested; user animation data not supplied"}
    safety.managed(output_path, suffix={".json"}).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def verify_regions(build, old, now, cmds, om):
    """Read saved native BS wiring/data and coincident partition boundaries."""
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import geometry, blendshape_map
    problems, checked, seen, seam_error, seam_pairs = [], [], {}, 0., 0
    captures={}
    for output in build.get('regional_outputs',[]):
        uid=output['result_uuid']; source_uid=output['source_uuid']
        if uid not in now:continue
        target=now[uid]
        mapping=output['source_vertex_ids']
        points=geometry.mesh_fn(target['shape'])[1].getPoints(om.MSpace.kWorld)
        if len(points)!=len(mapping):
            problems.append('Regional vertex correspondence size changed: '+target['path']);continue
        ranges=output.get('source_ranges',[{'source_uuid':source_uid,'start':0,'end':len(mapping)}])
        for owned in ranges:
            for index in range(owned['start'],owned['end']):
                point=points[index];key=(owned['source_uuid'],mapping[index])
                if key in seen:
                    seam_error=max(seam_error,point.distanceTo(seen[key]));seam_pairs+=1
                else:seen[key]=point
        bs=output['blendshape'].get('node')
        if not bs:continue
        source_bs=old[source_uid]['blendshapes'][0]
        if source_bs not in captures:
            data = build.get('regional_source_blendshapes', {}).get(source_bs)
            if data is not None:
                # JSON object keys are strings; component indices are integers.
                data['base_weights'] = {int(k): v for k, v in data['base_weights'].items()}
                for t in data['targets']:
                    t['weights'] = {int(k): v for k, v in t['weights'].items()}
            captures[source_bs] = data if data is not None else blendshape_map.capture(cmds,source_bs)
        source_data=captures[source_bs]; actual=blendshape_map.capture(cmds,bs)
        original={t['index']:t for t in source_data['targets']}
        local_problems=[]
        if actual['envelope']!=source_data['envelope'] or actual['envelope_input']!=source_data['envelope_input']:
            local_problems.append('envelope')
        actual_ids={t['index'] for t in actual['targets']}
        expected_ids={t['index'] for t in source_data['targets'] if any(set(i['ids']).intersection(mapping) for i in t['items'])}
        if actual_ids!=expected_ids:local_problems.append('target presence')
        for target_data in actual['targets']:
            expected=original[target_data['index']]
            if not target_driver_matches(target_data, expected):local_problems.append('driver or value')
            for item in target_data['items']:
                original_item=next(i for i in expected['items'] if i['index']==item['index'])
                if item['geometry']!=original_item['geometry']:local_problems.append('live expression input')
                expected_points=blendshape_map.remap_points(original_item,mapping)
                if (item['ids'] != [i for i,_ in expected_points] or
                    len(item['points']) != len(expected_points) or
                    any(max(abs(a-b) for a,b in zip(point,expected))>1e-8
                        for point,(_,expected) in zip(item['points'],expected_points))):
                    local_problems.append('corrective delta')
            expected_weights={i:expected['weights'][old_id] for i,old_id in enumerate(mapping) if old_id in expected['weights']}
            if any(abs(target_data['weights'].get(i,1.)-value)>1e-8 for i,value in expected_weights.items()):local_problems.append('target masks')
        if local_problems:problems.append(target['path']+': '+', '.join(sorted(set(local_problems))))
        checked.append({'output':target['path'],'targets':len(actual['targets']),'passed':not local_problems})
    if seam_error>1e-5:problems.append('Regional partition seam positions differ')
    return {'outputs':checked,'shared_boundary_vertices':seam_pairs,'max_seam_position_error':seam_error,'problems':problems}
