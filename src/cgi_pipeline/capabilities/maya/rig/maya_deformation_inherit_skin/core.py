"""Orchestrate atomic, component-scoped Maya skin inheritance."""

from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from .ownership import OwnershipAmbiguous, choose_owner
from .scene_ops import (
    compact_vertices, components, ensure_target_skin, local_influences, make_proxy,
    mesh_rows, output_paths, prune, set_skin_weights, skin_cluster, skin_weights,
    source_metrics, strip_namespaces, surface_distance, write_artifacts,
)


class NeedsAttention(RuntimeError):
    def __init__(self, message: str, output: dict[str, Any]):
        super().__init__(message)
        self.output = output


def _owner_override(owner_map: dict[str, str], target: dict[str, str], component: int,
                    sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    requested = owner_map.get(f"{target['transform']}#{component}", owner_map.get(target["transform"], ""))
    if not requested:
        return None
    matches = [row for row in sources if requested in {row["transform"], strip_namespaces(row["transform"])}]
    if len(matches) != 1:
        raise ValueError(f"Explicit owner {requested} resolved to {len(matches)} source meshes")
    return {"source": matches[0]["transform"], "score": 1.0, "score_gap": 1.0,
            "candidates": [], "override": True}


def inherit_skin(params: dict[str, Any], context) -> dict[str, Any]:
    cmds = context.extras.get("cmds_module")
    if cmds is None:
        raise RuntimeError("Maya execution context is required")
    mode, scope = params.get("mode", "diagnose_only"), params.get("apply_scope", "missing_only")
    maximum = params.get("max_influences", 0)
    owner_map, influence_map = params.get("owner_map") or {}, params.get("influence_map") or {}
    if mode not in {"diagnose_only", "apply_skin"} or scope not in {"missing_only", "all"}:
        raise ValueError("Unsupported mode or apply_scope")
    if not isinstance(owner_map, dict) or not isinstance(influence_map, dict):
        raise ValueError("owner_map and influence_map must be objects")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 0 <= maximum <= 256:
        raise ValueError("max_influences must be an integer between 0 and 256")
    reference, diagnostic_path, weights_path, output_path = output_paths(params, context)
    reference_group = params.get("reference_group") or ""
    target_group = params.get("target_group") or "|Group|Geometry|cache"
    if not reference_group.startswith("|") or not target_group.startswith("|"):
        raise ValueError("reference_group and target_group must be absolute DAG paths")

    prefix = f"__CGI_INHERIT_{uuid4().hex[:10]}__"
    namespace = prefix.rstrip("_")
    previous_selection = cmds.ls(selection=True, long=True) or []
    opened, target_chunk_closed, reference_node = False, False, ""
    diagnostic: dict[str, Any] = {
        "schema_version": "maya_deformation_inherit_skin.v1", "mode": mode,
        "apply_scope": scope, "reference_rig_path": str(reference),
        "reference_group": reference_group, "target_group": target_group,
        "ownership": [], "issues": [], "skipped": [],
    }
    arrays, created_artifacts, proxies = {}, [], {}
    try:
        new_nodes = cmds.file(
            str(reference), reference=True, namespace=namespace,
            mergeNamespacesOnClash=False, returnNewNodes=True, options="v=0;"
        ) or []
        if not new_nodes:
            raise RuntimeError("Reference load returned no Maya nodes")
        reference_node = str(cmds.referenceQuery(new_nodes[0], referenceNode=True))
        group_matches = [
            node for node in (cmds.ls(f"{namespace}:*", long=True, type="transform") or [])
            if strip_namespaces(node) == reference_group
        ]
        if len(group_matches) != 1:
            raise ValueError(f"reference_group resolved to {len(group_matches)} nodes")
        sources = []
        for row in mesh_rows(cmds, group_matches[0]):
            source_skin = skin_cluster(cmds, row["shape"], required=False)
            if source_skin:
                sources.append({**row, "skin": source_skin, **source_metrics(row["shape"])})
        if not sources:
            raise ValueError("reference_group contains no skinned source meshes")
        target_rows, targets = mesh_rows(cmds, target_group), []
        for row in target_rows:
            existing = skin_cluster(cmds, row["shape"], required=False)
            if scope == "missing_only" and existing:
                diagnostic["skipped"].append({"target": row["transform"], "reason": "already_skinned"})
            else:
                targets.append({**row, "existing_skin": existing})

        source_by_path, plan = {row["transform"]: row for row in sources}, []
        for target in targets:
            for index, component in enumerate(components(target["shape"])):
                measured = [{
                    "source": source["transform"], "name": source["transform"],
                    "vertex_count": source["vertex_count"], "centroid": source["centroid"],
                    "diagonal": source["diagonal"],
                    "surface_distance": surface_distance(source["shape"], component["samples"]),
                } for source in sources]
                try:
                    owner = _owner_override(owner_map, target, index, sources) or choose_owner(
                        {"name": target["transform"], "vertex_count": component["vertex_count"],
                         "centroid": component["centroid"], "diagonal": component["diagonal"]}, measured
                    )
                except (OwnershipAmbiguous, ValueError) as exc:
                    diagnostic["issues"].append({
                        "type": "AMBIGUOUS_OWNER", "target": target["transform"],
                        "component": index, "message": str(exc),
                        "candidates": getattr(exc, "candidates", []),
                    }); continue
                entry = {"target": target["transform"], "target_shape": target["shape"],
                         "component": index, "vertices": component["vertices"], **owner}
                plan.append(entry)
                diagnostic["ownership"].append({key: value for key, value in entry.items() if key != "vertices"})

        mapped_by_source: dict[str, list[str]] = {}
        for source_path in sorted({row["source"] for row in plan}):
            mapped, issues = local_influences(
                cmds, namespace, source_by_path[source_path]["skin"], influence_map
            )
            mapped_by_source[source_path] = mapped
            diagnostic["issues"].extend({"type": "INFLUENCE_MAPPING", "source": source_path,
                                          "message": message} for message in issues)
        if diagnostic["issues"]:
            cmds.file(removeReference=True, referenceNode=reference_node); reference_node = ""
            write_artifacts(diagnostic_path, weights_path, diagnostic, {})
            raise NeedsAttention(
                "Skin ownership or influence mapping requires an explicit decision",
                {"diagnostic_path": str(diagnostic_path), "weights_path": str(weights_path),
                 "output_path": "", "issue_count": len(diagnostic["issues"])},
            )

        proxies = {path: make_proxy(cmds, source_by_path[path], mapped_by_source[path], f"{prefix}{index}_")
                   for index, path in enumerate(sorted(mapped_by_source))}
        cmds.file(removeReference=True, referenceNode=reference_node); reference_node = ""
        cmds.undoInfo(openChunk=True, chunkName="maya_deformation_inherit_skin"); opened = True
        processed = []
        for target in targets:
            target_plan = [row for row in plan if row["target"] == target["transform"]]
            if not target_plan:
                continue
            influences = sorted({node for row in target_plan for node in mapped_by_source[row["source"]]})
            target_skin = ensure_target_skin(cmds, target, influences, maximum)
            for row in target_plan:
                proxy = proxies[row["source"]]
                cmds.select(proxy["transform"], replace=True)
                cmds.select(compact_vertices(target["shape"], row["vertices"]), add=True)
                cmds.copySkinWeights(
                    sourceSkin=proxy["skin"], destinationSkin=target_skin, noMirror=True,
                    surfaceAssociation="closestPoint", influenceAssociation=["name"],
                    normalize=True, selectedComponents=True,
                )
            influence_paths, values, vertex_count = skin_weights(target_skin, target["shape"])
            values = prune(values, vertex_count, len(influence_paths), maximum)
            if maximum:
                set_skin_weights(target_skin, target["shape"], values)
            for vertex in range(vertex_count):
                row_values = values[vertex * len(influence_paths):(vertex + 1) * len(influence_paths)]
                if any(not math.isfinite(value) or value < 0 for value in row_values):
                    raise RuntimeError(f"Invalid weight on {target['shape']}.vtx[{vertex}]")
                if not math.isclose(sum(row_values), 1.0, rel_tol=1e-5, abs_tol=1e-5):
                    raise RuntimeError(f"Unnormalized weight on {target['shape']}.vtx[{vertex}]")
            import numpy as np
            key = f"mesh_{len(processed):03d}"
            arrays[f"{key}_weights"] = np.asarray(values, dtype=np.float64).reshape(vertex_count, len(influence_paths))
            arrays[f"{key}_influences"] = np.asarray(influence_paths, dtype=str)
            arrays[f"{key}_target"] = np.asarray([target["transform"]], dtype=str)
            processed.append({"target": target["transform"], "skin_cluster": target_skin,
                              "vertex_count": vertex_count, "influence_count": len(influence_paths)})

        diagnostic["processed"] = processed
        diagnostic["summary"] = {"source_meshes": len(sources), "target_meshes": len(target_rows),
                                 "processed_meshes": len(processed),
                                 "skipped_meshes": len(diagnostic["skipped"])}
        if proxies:
            cmds.delete([row["transform"] for row in proxies.values()])
        cmds.undoInfo(closeChunk=True); opened = False; target_chunk_closed = True
        if mode == "diagnose_only":
            cmds.undo(); target_chunk_closed = False
            cmds.delete([row["transform"] for row in proxies.values()])
            proxies = {}
        write_artifacts(diagnostic_path, weights_path, diagnostic, arrays)
        created_artifacts.extend([diagnostic_path, weights_path])
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            cmds.file(rename=str(output_path))
            cmds.file(save=True, force=False,
                      type="mayaAscii" if output_path.suffix.lower() == ".ma" else "mayaBinary")
            target_chunk_closed = False
            proxies = {}
        return {"output_path": str(output_path or ""), "weights_path": str(weights_path),
                "diagnostic_path": str(diagnostic_path), **diagnostic["summary"]}
    except NeedsAttention:
        raise
    except Exception:
        if opened:
            cmds.undoInfo(closeChunk=True); cmds.undo()
            opened = False
        elif target_chunk_closed:
            cmds.undo()
            target_chunk_closed = False
        if output_path and output_path.exists():
            output_path.unlink()
        for path in created_artifacts:
            if path.exists():
                path.unlink()
        raise
    finally:
        if proxies:
            try:
                cmds.delete([row["transform"] for row in proxies.values()
                             if cmds.objExists(row["transform"])])
            except Exception:
                pass
        if reference_node:
            try:
                cmds.file(removeReference=True, referenceNode=reference_node)
            except Exception:
                pass
        try:
            cmds.select(previous_selection, replace=True) if previous_selection else cmds.select(clear=True)
        except Exception:
            pass
