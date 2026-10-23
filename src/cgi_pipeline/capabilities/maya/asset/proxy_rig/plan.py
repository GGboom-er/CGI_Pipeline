"""Pure validation of the AI-authored plan against one structural scene snapshot."""
from __future__ import annotations

import math
import re

from .inventory import POLICY_VERSION


def partition_merge_candidates(rows):
    """Deterministic cheap partition; geometry/bind matrices still need native preflight.

    Names/bounds nominate categories before this step. Independent switching is
    a boundary; the expanded union of skin influences is not.
    """
    buckets = {}
    for row in rows:
        if row["protected_reasons"]:
            continue
        key = (row.get('parent'),row["visibility_signature"], str(row.get("memberships", {})),
               row["skin"]["method"] if row["skin"] else None,
               row["skin"]["normalize"] if row["skin"] else None)
        buckets.setdefault(key, []).append(row)
    result = []
    for key in sorted(buckets, key=str):
        # Related joints first, then spatial center and path. No random ordering.
        ordered = sorted(buckets[key], key=lambda r: (
            tuple(r["skin"]["influences"]) if r["skin"] else (),
            tuple(r["bbox"]), r["path"]))
        result.append(ordered)
    return result


def _list(plan, key):
    value = plan.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(key + " must be an array of objects")
    return value


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(label + " contains duplicate source identities")


def _name(value, character):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("output_name must be one plain Maya node name")
    if not value.startswith(character + "_"):
        raise ValueError("output_name must preserve the canonical character prefix")
    return value


def validate(snapshot, plan, character=""):
    if not isinstance(plan, dict) or not plan:
        raise ValueError("An explicit scene plan is required after inspection and visual review")
    if plan.get("policy_version") != POLICY_VERSION:
        raise ValueError("Plan policy_version is missing or stale")
    if plan.get("scene_path", "").replace("\\", "/") != snapshot["scene_path"]:
        raise ValueError("Plan scene_path does not identify the current scene")
    if plan.get("scene_fingerprint") != snapshot["scene_fingerprint"]:
        raise ValueError("Plan scene_fingerprint is stale; inspect and reconsider affected classes")
    character = character or plan.get("character", "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", character):
        raise ValueError("character must be the canonical ASCII character name")
    if plan.get("character") != character:
        raise ValueError("Plan character differs from this request")
    by_id = {item["uuid"]: item for item in snapshot["meshes"]}
    discard_by_id = {**{item["uuid"]: item for item in snapshot.get("outside_cache", [])}, **by_id}
    scope = plan.get("scope", list(by_id))
    if not isinstance(scope, list) or not scope or any(not isinstance(x, str) for x in scope):
        raise ValueError("scope must be a nonempty array of output transform UUIDs")
    _unique(scope, "scope")
    if set(scope) - by_id.keys():
        raise ValueError("scope includes missing source UUIDs")
    reduced, merged, retained, names = [], [], [], []
    discarded = _list(plan, "discard")
    discard_ids = []
    for entry in discarded:
        source = entry.get("uuid")
        if not isinstance(source, str) or source not in discard_by_id:
            raise ValueError("discard requires an inspected output UUID")
        if not entry.get("reason") or not entry.get("evidence"):
            raise ValueError("discard requires a reason and decision evidence")
        if discard_by_id[source].get("status", "candidate") != "candidate":
            raise ValueError("discard targets an effective auxiliary/proxy dependency")
        discard_ids.append(source)
    _unique(discard_ids, "discard")
    reductions, merges = _list(plan, "reduce_entries"), _list(plan, "merge_groups")
    mapped = _list(plan, 'region_entries')
    mapped_ids = []
    region_owners={}
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.regions import validate as validate_regions
    for entry in mapped:
        uid = entry.get('source_uuid')
        if uid not in by_id or not entry.get('reason'):
            raise ValueError('Region entry requires an inspected source and decision reason')
        validate_regions(entry.get('regions'), by_id[uid]['faces'])
        from cgi_pipeline.capabilities.maya.asset.mesh_reduce.quality import validate as validate_quality
        validate_quality(entry.get('quality'))
        mapped_ids.append(uid)
        for n in sorted({r['output_name'] for r in entry['regions']}):
            region_owners.setdefault(_name(n,character),[]).append(uid)
    for n,ids in region_owners.items():
        if len(ids)>1:
            rows=[by_id[uid] for uid in ids]
            signature=lambda r:(r['parent'],r['visibility_signature'],str(r.get('memberships',{})))
            if len({signature(r) for r in rows})!=1 or any(r.get('blendshapes') or r.get('memberships',{}).get('component_sets') for r in rows):
                raise ValueError('Shared regional output requires compatible display/parent and no expression/component sets')
    names.extend(sorted(region_owners))
    _unique(mapped_ids, 'region_entries')
    for entry in reductions:
        source = entry.get("source_uuid")
        ratio = entry.get("keep_face_ratio")
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not (
                math.isfinite(ratio) and 0 < ratio < 1):
            raise ValueError("keep_face_ratio must be a finite number strictly between zero and one")
        reduced.append(source)
        names.append(_name(entry.get("output_name"), character))
        if not entry.get("reason"):
            raise ValueError("Each reduction class entry requires its decision reason")
    for group in merges:
        sources = group.get("sources")
        if not isinstance(sources, list) or len(sources) < 2:
            raise ValueError("Each merge group requires at least two source UUIDs")
        _unique(sources, "merge group")
        merged.extend(sources)
        names.append(_name(group.get("output_name"), character))
        if not group.get("reason"):
            raise ValueError("Each merge group requires its compatibility decision reason")
    for key in ("keep", "protect"):
        for entry in _list(plan, key):
            if not entry.get("reason"):
                raise ValueError(key + " requires an explicit reason")
            retained.append(entry.get("uuid"))
    for values, label in ((reduced, "reduce_entries"), (merged, "merge_groups"), (retained, "keep/protect")):
        if any(not isinstance(value, str) for value in values):
            raise ValueError(label + " requires explicit source UUIDs")
        _unique(values, label)
    if set(mapped_ids) & (set(reduced) | set(merged)):
        raise ValueError('Region sources cannot also use ordinary reduce/merge entries')
    changed = set(reduced) | set(merged) | set(mapped_ids)
    if changed & set(retained):
        raise ValueError("A protected/kept source cannot also be reduced or merged")
    if set(discard_ids) & (changed | set(retained)):
        raise ValueError("discard overlaps processing, keep or protection decisions")
    covered = changed | set(retained) | (set(discard_ids) & set(by_id))
    if covered != set(scope):
        raise ValueError("Every scoped output must have exactly one keep/protect or processing decision")
    if len(names) != len(set(names)):
        raise ValueError("Planned temporary/final output names must be unique")
    for source in sorted(changed):
        if by_id[source]["protected_reasons"] and source not in mapped_ids:
            raise ValueError(by_id[source]["path"] + ": unsupported dependency must be protected: " +
                             ", ".join(by_id[source]["protected_reasons"]))
    for group in merges:
        members = [by_id[source] for source in group["sources"]]
        signature = lambda item: (item["visibility_signature"], bool(item["skin"]),
                                  item["skin"]["method"] if item["skin"] else None,
                                  item["skin"]["normalize"] if item["skin"] else None,
                                  str(item.get("memberships", {})))
        if len({signature(item) for item in members}) != 1:
            raise ValueError("Merge members differ in parent, display switching or skin method")
    weights = dict(plan.get("weights") or {})
    if weights.get("method", "dair_inspired") not in ("dair_inspired", "ng_proportional"):
        raise ValueError("Unsupported weight cleanup method")
    maximum, threshold = weights.get("max_influences", 12), weights.get("threshold", .001)
    if type(maximum) is not int or maximum < 1:
        raise ValueError("max_influences must be a positive integer")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not (
            math.isfinite(threshold) and 0 <= threshold < 1):
        raise ValueError("weight threshold must be a finite number in [0,1)")
    cleanup = plan.get("cleanup_unused_materials", False)
    searches=plan.get('geometry_search',{})
    if not isinstance(searches,dict):raise ValueError('geometry_search must map final output names to search options')
    available={g['output_name'] for g in merges} | {r['output_name'] for r in reductions if r['source_uuid'] not in set(merged)}
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.search import validate as validate_search
    if set(searches)-available:raise ValueError('geometry_search refers to an unknown ordinary output')
    searches={name:validate_search(options) for name,options in searches.items()}
    output_counts={g['output_name']:sum(by_id[u]['faces'] for u in g['sources']) for g in merges}
    output_counts.update({r['output_name']:by_id[r['source_uuid']]['faces'] for r in reductions if r['source_uuid'] not in set(merged)})
    for name,options in searches.items():
        groups=options.get('work_groups',[])
        if groups and {f for g in groups for f in g['faces']}!=set(range(output_counts[name])):
            raise ValueError('Visual work groups must cover the pooled output source faces')
    retain_sources = plan.get("retain_sources", True)
    if not isinstance(retain_sources, bool):
        raise ValueError("retain_sources must be boolean")
    if not isinstance(cleanup, bool):
        raise ValueError("cleanup_unused_materials must be boolean")
    return {"character": character, "by_id": by_id, "scope": scope,
            "changed": sorted(changed), "reductions": reductions, "merges": merges,
            "region_entries": mapped,
            "geometry_search": searches,
            "keep": _list(plan, "keep"), "protect": _list(plan, "protect"),
            "discard": discarded, "discard_by_id": discard_by_id,
            "cleanup_unused_materials": cleanup,
            "retain_sources": retain_sources,
            "weights": {**weights, "method": weights.get("method", "dair_inspired"),
                        "max_influences": maximum, "threshold": threshold},
            "full_scene_coverage": set(scope) == set(by_id),
            "unscoped_meshes": len(by_id) - len(scope)}
