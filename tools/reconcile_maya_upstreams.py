"""Reconcile extracted Maya upstream tools with the canonical CGI catalog."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "integrations" / "maya" / "upstreams" / "candidate_inventory.json"
DEFAULT_CATALOG = ROOT / "src" / "cgi_pipeline" / "data" / "capabilities.json"


ALIASES: dict[str, set[str]] = {
    "maya.asset.exec_code": {"execute_code", "execute_python", "run_script", "script_run", "script_execute", "write_module", "execute_mel"},
    "maya.asset.maya_apply_materials": {"assign_material", "maya_assign_material"},
    "maya.asset.maya_capture_viewport": {"capture_viewport", "maya_viewport_capture", "viewport_capture", "capture_ui"},
    "maya.asset.maya_create_primitive": {
        "create_primitive", "maya_create_primitive", "create_polygon_primitive", "modeling_create_polygon_primitive",
        "create_cube", "create_sphere", "create_cylinder", "create_plane", "create_object",
    },
    "maya.asset.maya_export_abc": {"export_alembic", "export_shot_alembic"},
    "maya.asset.maya_freeze_transforms": {"freeze_transforms", "modeling_freeze_transforms"},
    "maya.asset.maya_get_object_info": {
        "get_object_info", "nodes_info", "describe_node", "get_bounding_box", "mesh_info", "curve_info",
    },
    "maya.asset.maya_get_scene_info": {"get_scene_info", "scene_info", "get_scene_summary", "scene_snapshot", "debug_scene_snapshot"},
    "maya.asset.maya_import_abc": {"import_file", "maya_import_file", "scene_import", "import_to_scene"},
    "maya.asset.maya_open_scene": {"open_scene", "scene_open"},
    "maya.asset.maya_select_objects": {"select_object", "select_by_type", "select_by_material", "viewport_focus"},
    "maya.asset.maya_set_attribute": {"set_attribute", "set_object_attribute"},
    "maya.asset.save_scene": {"save_scene", "scene_save", "scene_save_as"},
    "maya.rig.skin.query_weights": {"get_skin_weights", "skin_weights_get", "query_skin_cluster", "skin_influences"},
    "maya.scene.attributes.get_many": {"get_attribute", "get_object_attributes", "get_node_attributes", "attributes_get"},
    "maya.scene.attributes.inspect": {"list_attributes"},
    "maya.scene.attributes.set_many": {"set_attribute", "set_object_attribute", "attributes_set", "set_material_attribute"},
    "maya.scene.connections.edit_many": {"connect_attr", "disconnect_attr", "connections_connect", "connections_disconnect"},
    "maya.scene.connections.inspect_many": {
        "list_connections", "connections_list", "connections_get", "connections_history", "get_material_connections", "list_history",
    },
    "maya.scene.graph.snapshot_bounded": {"scene_inspect", "query_scene_topology"},
    "maya.scene.graph.validate_delta": {"scene_assert", "scene_validate", "scene_review"},
    "maya.scene.hierarchy.edit_many": {"parent_object", "nodes_parent", "group_objects", "organize_objects"},
    "maya.scene.nodes.create": {"create_node", "nodes_create", "create_locator", "create_camera", "camera_create", "maya_create_camera"},
    "maya.scene.nodes.delete": {"delete_node", "delete_objects", "delete_object", "nodes_delete"},
    "maya.scene.nodes.duplicate_many": {"duplicate_object", "nodes_duplicate", "array_instances", "arrange_in_grid", "arrange_in_circle"},
    "maya.scene.nodes.find": {
        "list_objects", "list_objects_by_type", "list_scene_objects", "nodes_list", "find_by_pattern", "search_nodes_by_attribute",
    },
    "maya.scene.nodes.inspect_many": {"nodes_info", "describe_node", "make_node_ref", "resolve_node_ref"},
    "maya.scene.nodes.rename": {"rename_object", "nodes_rename"},
    "maya.scene.selection.get": {"get_selection", "selection_get", "get_selection_context", "selection_get_components"},
    "maya.scene.selection.set": {"set_selection", "selection_set", "selection_clear", "clear_selection_list", "selection_set_components"},
    "maya.scene.transforms.get": {"get_transform"},
    "maya.scene.transforms.set_many": {"set_transform", "maya_transform", "set_object_transform_attributes"},
    "pipeline.session.discover": {"list_sessions", "add_session", "get_session_info", "maya_session", "health_check"},
}

DEFER_PATTERNS: dict[str, str] = {
    r"export_animation_curves|import_animation_curves": "requires_sandboxed_curve_transfer_contract",
    r"bifrost": "specialized_bifrost",
    r"debugpy|reload_modules|attach_project|run_entrypoint|ui_|maya_setup_guide": "development_or_ui_control",
    r"vision3d|worldlabs|learn_pattern|search_maya_docs": "external_or_learning_service",
    r"deadline|render_job|render_farm|submit_to_|farm": "site_specific_render_farm",
    r"generate_scene|create_advanced_model|aesthetics|(^|_)plan$": "generative_or_composite_workflow",
    r"assembl|setup_guide": "legacy_or_setup_helper",
    r"^modeling$|^operation$|^operations$|^io$|^session$|^run_check$": "generic_dispatch_wrapper",
}

BUNDLE_RULES: tuple[tuple[str, str], ...] = (
    (r"bake_constraints|bake_simulation", "maya.animation.bake"),
    (r"keyframe|keyframes", "maya.animation.keyframes.manage"),
    (r"current_time|frame_range|timeline|scene_time|frame_rate", "maya.animation.time.manage"),
    (r"anim_curve|animation_curve", "maya.animation.curves.manage"),
    (r"expression", "maya.animation.expressions.manage"),
    (r"pose", "maya.animation.poses.manage"),
    (r"skin_cluster_bind|cluster_bind|skin_cluster_unbind", "maya.rig.skin.bind"),
    (r"set_skin_weights", "maya.rig.skin.set_weights"),
    (r"copy_skin_weights", "maya.rig.skin.copy_weights"),
    (r"blend_shape", "maya.rig.blendshape.manage"),
    (r"assign_deformer|add_deformer", "maya.rig.deformers.manage"),
    (r"constraint", "maya.rig.constraints.manage"),
    (r"create_joint|mirror_joints|joint_limit|joint_orient", "maya.rig.joints.manage"),
    (r"ik_handle|ik_fk_blend", "maya.rig.ik.manage"),
    (r"rig_control|guide_curve", "maya.rig.controls.create"),
    (r"driven_key", "maya.rig.driven_keys.manage"),
    (r"rig_framework|rig_state", "maya.rig.framework.inspect"),
    (r"mesh_vertices|mesh_edge|poly_count|evaluate_mesh|mesh_info", "maya.modeling.mesh.inspect"),
    (r"extrude_faces|^extrude$|bevel_components|bridge_edge_loops|insert_edge_loop|delete_faces|move_components|merge_vertices", "maya.modeling.components.edit"),
    (r"boolean_mesh|combine_meshes|separate_mesh", "maya.modeling.mesh.combine"),
    (r"smooth_mesh|^smooth$|apply_subdivision", "maya.modeling.mesh.subdivide"),
    (r"cleanup_mesh|triangulate|mirror_mesh|extract_faces|create_proxy_mesh", "maya.modeling.mesh.process"),
    (r"transfer_attributes|apply_symmetry", "maya.modeling.mesh.transfer"),
    (r"auto_uv|copy_uvs|uv_set|uv_info|uv_shell|normalize_uvs|project_uvs|unfold_uvs", "maya.modeling.uv.manage"),
    (r"create_curve|curve_cvs|^cvs$|curve_modeling|loft_sections|lathe_profile", "maya.modeling.curves.manage"),
    (r"pivot|delete_history", "maya.modeling.transforms.finalize"),
    (r"nurbs_primitive", "maya.modeling.nurbs.create"),
    (r"create_material|list_materials|material_attribute|shader_assignment|shading_group|default_material|material_color", "maya.lookdev.materials.manage"),
    (r"material_preset|load_material|save_material", "maya.lookdev.materials.presets"),
    (r"create_light|hdri_dome|three_point_rig|light_rig", "maya.lookdev.lights.manage"),
    (r"bake_ambient_occlusion|bake_lighting|bake_textures|bake_sets|transfer_maps", "maya.lookdev.bake.manage"),
    (r"color_management|color_spaces", "maya.lookdev.color.manage"),
    (r"render_settings|render_quality|scene_render_stats|setup_hdr_arnold", "maya.render.settings.manage"),
    (r"render_frame|render_sequence|render_still", "maya.render.execute"),
    (r"playblast", "maya.render.playblast"),
    (r"viewport_camera|look_through_camera|set_camera|camera_orbit|list_cameras", "maya.render.cameras.manage"),
    (r"display_layer", "maya.scene.display_layers.manage"),
    (r"checkpoint|rollback", "maya.scene.checkpoints.manage"),
    (r"scene_undo|scene_redo|^undo$|^redo$", "maya.scene.history.manage"),
    (r"set_visibility|lock_object", "maya.scene.nodes.state"),
    (r"convert_components|get_components|set_components", "maya.scene.components.select"),
    (r"asset_metadata|tag_asset_metadata", "maya.asset.metadata.manage"),
    (r"search_assets|resolve_asset|read_asset_head", "maya.asset.registry.query"),
    (r"publish_asset|publish_usd_revision|sync_usd_revision", "maya.asset.publish.manage"),
    (r"^export$|export_fbx|export_obj|export_camera|export_shot_fbx", "maya.asset.export.manage"),
    (r"import_fbx", "maya.asset.import.manage"),
    (r"get_shot_info", "maya.asset.shots.inspect"),
    (r"export_animation_curves|import_animation_curves", "maya.asset.animation_curves.transfer"),
    (r"export_preset", "maya.asset.export_presets.manage"),
    (r"set_project", "maya.scene.project.set"),
    (r"add_attribute|delete_attribute", "maya.scene.attributes.schema"),
    (r"get_dag_path", "maya.scene.nodes.resolve"),
    (r"scene_measure|^measure$", "maya.scene.measurements.inspect"),
    (r"new_scene", "maya.scene.file.new"),
    (r"file_exists", "maya.runtime.files.inspect"),
    (r"list_plugins|load_plugin|unload_plugin", "maya.runtime.plugins.manage"),
    (r"introspect_|list_mel_procedures|get_script_node", "maya.runtime.introspection.query"),
    (r"session_stats|reset_session_stats", "maya.runtime.metrics.manage"),
    (r"create_gravity_field|dynamic_field|rigid_body|list_dynamics", "maya.dynamics.fields.manage"),
)

PREFIXES = (
    "maya_", "modeling_", "scene_", "nodes_", "attributes_", "connections_", "selection_",
    "skin_", "animation_", "shading_", "viewport_", "mesh_", "curve_", "script_",
)


def normalize_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    changed = True
    while changed:
        changed = False
        for prefix in PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix):]
                changed = True
                break
    aliases = {
        "new": "new_scene",
        "open": "open_scene",
        "save": "save_scene",
        "save_as": "save_scene",
        "info": "get_scene_info",
        "vertices": "get_mesh_vertices",
        "evaluate": "evaluate_mesh",
        "boolean": "boolean_mesh",
        "combine": "combine_meshes",
        "separate": "separate_mesh",
        "bevel": "bevel_components",
        "bridge": "bridge_edge_loops",
        "bind": "skin_cluster_bind",
        "unbind": "skin_cluster_unbind",
        "copy_weights": "copy_skin_weights",
        "weights_set": "set_skin_weights",
        "weights_get": "get_skin_weights",
        "set_time": "set_current_time",
        "get_time_range": "get_frame_range",
        "set_time_range": "set_timeline",
    }
    return aliases.get(name, name)


def _covered_api(name: str, catalog_ids: set[str]) -> str | None:
    normalized = normalize_name(name)
    for api_id, aliases in ALIASES.items():
        if api_id in catalog_ids and normalized in {normalize_name(item) for item in aliases}:
            return api_id
    return None


def _defer_reason(name: str) -> str | None:
    normalized = normalize_name(name)
    for pattern, reason in DEFER_PATTERNS.items():
        if re.search(pattern, normalized):
            return reason
    return None


def _domain(group: str, name: str) -> str:
    text = f"{group} {name}".lower()
    if any(token in text for token in ("skin", "rig", "joint", "constraint", "blend_shape", "deformer", "pose", "ik_")):
        return "rig"
    if any(token in text for token in ("animation", "keyframe", "timeline", "anim_curve", "expression")):
        return "animation"
    if any(token in text for token in ("material", "shading", "light", "texture", "color_space")):
        return "lookdev"
    if any(token in text for token in ("render", "playblast", "camera")):
        return "render"
    if any(token in text for token in ("modeling", "mesh", "geometry", "uv", "curve", "pivot", "subdivision")):
        return "modeling"
    if any(token in text for token in ("dynamics", "rigid_body", "gravity")):
        return "dynamics"
    if any(token in text for token in ("asset", "publish", "export", "import")):
        return "asset"
    if any(token in text for token in ("development", "core", "script", "session", "plugin", "introspect")):
        return "runtime"
    return "scene"


def _suggested_api(group: str, name: str) -> str:
    semantic = normalize_name(name)
    return f"maya.{_domain(group, name)}.{semantic}"


def _bundled_api(name: str) -> str | None:
    normalized = normalize_name(name)
    for pattern, api_id in BUNDLE_RULES:
        if re.search(pattern, normalized):
            return api_id
    return None


def _priority(api_id: str | None, decision: str) -> str:
    if decision == "merge":
        return "covered"
    if decision == "defer" or not api_id:
        return "P3"
    if api_id.startswith(("maya.animation.", "maya.rig.", "maya.scene.components", "maya.scene.measurements")):
        return "P1"
    if api_id.startswith(("maya.modeling.", "maya.lookdev.", "maya.render.", "maya.scene.")):
        return "P2"
    return "P3"


def reconcile(inventory: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    catalog_by_id = {row["api_id"]: row for row in catalog}
    catalog_ids = set(catalog_by_id)
    rows: list[dict[str, Any]] = []
    for candidate in inventory["candidates"]:
        row = dict(candidate)
        covered = _covered_api(row["name"], catalog_ids)
        deferred = _defer_reason(row["name"])
        if covered:
            row.update(decision="merge", canonical_api_id=covered, reason="covered_by_existing_canonical_api")
        elif deferred:
            row.update(decision="defer", canonical_api_id=None, reason=deferred)
        elif bundled := _bundled_api(row["name"]):
            row.update(decision="accept", canonical_api_id=bundled, reason="high_confidence_capability_bundle")
        else:
            row.update(
                decision="unresolved",
                canonical_api_id=_suggested_api(row["group"], row["name"]),
                reason="requires_contract_review",
            )
        spec = catalog_by_id.get(row.get("canonical_api_id"))
        row["implementation_status"] = (
            (spec.get("implementation") or {}).get("status", "unknown")
            if spec else "not_reconciled"
        )
        row["verification_status"] = (
            (spec.get("verification") or {}).get("level", "untested")
            if spec else "not_reconciled"
        )
        rows.append(row)

    clusters: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        key = (row["decision"], row.get("canonical_api_id") or normalize_name(row["name"]))
        clusters[key].append(row["candidate_id"])

    cluster_rows = []
    for (decision, canonical), ids in sorted(clusters.items()):
        spec = catalog_by_id.get(canonical)
        cluster_rows.append({
            "decision": decision,
            "canonical_api_id": canonical,
            "priority": _priority(canonical, decision),
            "candidate_ids": ids,
            "candidate_count": len(ids),
            "implementation_status": (
                (spec.get("implementation") or {}).get("status", "unknown")
                if spec else "not_reconciled"
            ),
            "verification_status": (
                (spec.get("verification") or {}).get("level", "untested")
                if spec else "not_reconciled"
            ),
        })
    accepted_clusters = [row for row in cluster_rows if row["decision"] == "accept"]
    return {
        "schema_version": 1,
        "candidate_count": len(rows),
        "summary": {
            "candidate_decisions": dict(Counter(row["decision"] for row in rows)),
            "semantic_clusters": dict(Counter(row["decision"] for row in cluster_rows)),
            "accepted_priorities": dict(Counter(row["priority"] for row in cluster_rows if row["decision"] == "accept")),
            "accepted_implementation": dict(Counter(row["implementation_status"] for row in accepted_clusters)),
            "accepted_verification": dict(Counter(row["verification_status"] for row in accepted_clusters)),
            "covered_canonical_apis": len({row["canonical_api_id"] for row in rows if row["decision"] == "merge"}),
        },
        "clusters": cluster_rows,
        "candidates": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    result = reconcile(inventory, catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
