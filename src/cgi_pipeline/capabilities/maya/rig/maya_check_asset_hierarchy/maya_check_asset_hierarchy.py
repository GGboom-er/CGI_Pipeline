import time
import traceback
import fnmatch

import maya.cmds as cmds

from cgi_pipeline.core.bootstrap import cfg as _cfg
from cgi_pipeline.core.config_loader import load_project_config
from cgi_pipeline.core.receipt import make_receipt


API_ID = "maya_check_asset_hierarchy"
INVALID_CODE = "ASSET_HIERARCHY_INVALID"
DEFAULT_CAMERA_TRANSFORMS = {"|persp", "|top", "|front", "|side"}


def _required_root_from_config(project, stage):
    cfg = load_project_config(project)
    stage_cfg = (cfg.get("stages") or {}).get(stage)
    if not stage_cfg:
        raise ValueError(f"项目配置中不存在 stage: {stage}")

    geom_roots = stage_cfg.get("geom_roots") or []
    if not geom_roots:
        raise ValueError(f"项目配置 stages.{stage}.geom_roots 为空")

    required_root = str(geom_roots[0]).strip()
    if not required_root:
        raise ValueError(f"项目配置 stages.{stage}.geom_roots[0] 为空")
    return required_root, [str(root).strip() for root in geom_roots[1:] if str(root).strip()]


def _valid_meshes_under(root):
    if not cmds.objExists(root):
        return []
    meshes = cmds.listRelatives(
        root,
        allDescendents=True,
        type="mesh",
        fullPath=True,
    ) or []
    valid = []
    for mesh in meshes:
        try:
            if not cmds.getAttr(mesh + ".intermediateObject"):
                valid.append(mesh)
        except Exception:
            valid.append(mesh)
    return valid


def _extra_top_nodes():
    assemblies = cmds.ls(assemblies=True, long=True) or []
    return sorted(
        node for node in assemblies
        if node not in DEFAULT_CAMERA_TRANSFORMS and node != "|Group"
    )


def _long_path(node):
    matches = cmds.ls(node, long=True) or []
    return matches[0] if matches else node


def _short_name(path):
    return path.split("|")[-1].split(":")[-1]


def _top_node(path):
    parts = [part for part in str(path).split("|") if part]
    return f"|{parts[0]}" if parts else ""


def _is_under(path, root):
    return path == root or path.startswith(root + "|")


def _dedupe(nodes):
    out = []
    seen = set()
    for node in nodes or []:
        if not node or not cmds.objExists(node):
            continue
        long_name = _long_path(node)
        if long_name in seen:
            continue
        seen.add(long_name)
        out.append(long_name)
    return out


def _matching_transforms(pattern):
    matches = cmds.ls(pattern, long=True, type="transform") or []
    if matches or not any(token in pattern for token in "*?[]"):
        return _dedupe(matches)

    all_transforms = cmds.ls(type="transform", long=True) or []
    return _dedupe(
        node for node in all_transforms
        if fnmatch.fnmatchcase(node, pattern)
    )


def _has_valid_mesh(root):
    return bool(_valid_meshes_under(root))


def _configured_source_roots(patterns, required_root):
    roots = []
    for pattern in patterns or []:
        for node in _matching_transforms(pattern):
            if _is_under(node, required_root):
                continue
            if _has_valid_mesh(node):
                roots.append(node)
    return roots


def _legacy_geo_roots(required_root, fallback_patterns):
    roots = []
    for pattern in fallback_patterns or []:
        for node in _matching_transforms(pattern):
            long_name = _long_path(node)
            if _short_name(long_name) != "geo":
                continue
            if _is_under(long_name, required_root):
                continue
            if _has_valid_mesh(long_name):
                roots.append(long_name)
    for node in cmds.ls(type="transform", long=True) or []:
        long_name = _long_path(node)
        if not _short_name(long_name).startswith("RIG_geo"):
            continue
        if _is_under(long_name, required_root) or _is_under(long_name, "|Group|Geometry|RIG_geo"):
            continue
        if _has_valid_mesh(long_name):
            roots.append(long_name)
    return _drop_ancestor_roots(roots)


def _nonstandard_cache_roots(required_root):
    roots = []
    for node in cmds.ls(type="transform", long=True) or []:
        long_name = _long_path(node)
        if _short_name(long_name) != "cache":
            continue
        if _is_under(long_name, required_root):
            continue
        if _has_valid_mesh(long_name):
            roots.append(long_name)
    return roots


def _drop_ancestor_roots(roots):
    selected = []
    for root in sorted(_dedupe(roots), key=lambda item: item.count("|"), reverse=True):
        if any(_is_under(child, root) for child in selected):
            continue
        selected.append(root)
    return sorted(selected)


def _candidate_source_roots(required_root, fallback_patterns):
    roots = []
    roots.extend(_nonstandard_cache_roots(required_root))
    roots.extend(_configured_source_roots(fallback_patterns, required_root))
    return _drop_ancestor_roots(roots)


def _safe_delete_transform(node):
    if not node or not cmds.objExists(node):
        return False
    if cmds.nodeType(node) != "transform":
        return False
    children = cmds.listRelatives(node, children=True, fullPath=True) or []
    if not children:
        return True

    descendants = cmds.listRelatives(node, allDescendents=True, fullPath=True) or []
    for child in children + descendants:
        if not child or not cmds.objExists(child):
            continue
        if cmds.nodeType(child) != "displayPoints":
            return False
    return True


def _classify_top_nodes(extra_top_nodes):
    safe_delete = []
    manual_review = []
    for node in extra_top_nodes or []:
        if _safe_delete_transform(node):
            safe_delete.append(node)
        else:
            manual_review.append(node)
    return safe_delete, manual_review


def _expected_rig_root(required_root, legacy_geo_roots, candidate_source_roots):
    if cmds.objExists("|Group|Geometry|RIG_geo"):
        return _long_path("|Group|Geometry|RIG_geo")
    if legacy_geo_roots:
        return legacy_geo_roots[0]
    if cmds.objExists(required_root):
        return _long_path(required_root)
    if candidate_source_roots:
        return candidate_source_roots[0]
    return required_root


def _as_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _format_failure(result):
    reasons = []
    phase = result.get("phase") or "post_sync"
    if phase == "pre_sync":
        if not result.get("active_rig_root_exists"):
            reasons.append(f"缺少可用于同步的旧绑定几何根 {result.get('active_rig_root') or '-'}")
        elif result.get("active_rig_mesh_count", 0) <= 0:
            reasons.append(f"旧绑定几何根 {result.get('active_rig_root')} 下没有有效 mesh")
    elif not result["required_root_exists"]:
        reasons.append(f"缺少标准根 {result['required_root']}")
    elif result["cache_mesh_count"] <= 0:
        reasons.append(f"标准根 {result['required_root']} 下没有有效 mesh")

    manual_top_nodes = result.get("manual_review_top_nodes") or []
    if manual_top_nodes:
        reasons.append(f"顶层存在需人工复核的非空节点 {len(manual_top_nodes)} 个")

    return "；".join(reasons) if reasons else "资产层级不符合项目规范"


def execute(payload):
    t0 = time.time()
    params = payload.get("parameters", {}) or {}
    project = payload.get("project") or params.get("project") or _cfg.DEFAULT_PROJECT
    stage = str(params.get("stage") or "rig").strip()
    phase = str(params.get("phase") or "post_sync").strip()
    block_on_fail = _as_bool(params.get("block_on_fail"), True)
    block_extra_top_nodes = _as_bool(params.get("block_extra_top_nodes"), False)
    source_path = payload.get("source_path") or cmds.file(query=True, sceneName=True) or ""

    try:
        required_root, fallback_patterns = _required_root_from_config(project, stage)
        root_exists = bool(cmds.objExists(required_root))
        cache_mesh_count = len(_valid_meshes_under(required_root)) if root_exists else 0
        extra_top_nodes = _extra_top_nodes()
        safe_delete_top_nodes, manual_review_top_nodes = _classify_top_nodes(extra_top_nodes)
        legacy_geo_roots = _legacy_geo_roots(required_root, fallback_patterns)
        candidate_source_roots = _candidate_source_roots(required_root, fallback_patterns)
        active_rig_root = _expected_rig_root(required_root, legacy_geo_roots, candidate_source_roots)
        active_rig_root_exists = bool(active_rig_root and cmds.objExists(active_rig_root))
        active_rig_mesh_count = (
            len(_valid_meshes_under(active_rig_root)) if active_rig_root_exists else 0
        )

        extra_top_ok = (not manual_review_top_nodes) or (not block_extra_top_nodes)
        if phase == "pre_sync":
            passed = active_rig_root_exists and active_rig_mesh_count > 0 and extra_top_ok
        else:
            passed = root_exists and cache_mesh_count > 0 and extra_top_ok

        issues = []
        if phase == "pre_sync":
            if not active_rig_root_exists:
                issues.append({
                    "type": "missing_active_rig_root",
                    "node": active_rig_root,
                    "severity": "blocking",
                })
            if active_rig_root_exists and active_rig_mesh_count <= 0:
                issues.append({
                    "type": "empty_active_rig_root",
                    "node": active_rig_root,
                    "severity": "blocking",
                })
        else:
            if not root_exists:
                issues.append({
                    "type": "missing_cache_root",
                    "node": required_root,
                    "severity": "blocking",
                })
            if root_exists and cache_mesh_count <= 0:
                issues.append({
                    "type": "empty_cache_root",
                    "node": required_root,
                    "severity": "blocking",
                })
        for node in legacy_geo_roots:
            issues.append({
                "type": "legacy_geo_root",
                "node": node,
                "severity": "blocking",
            })
        for node in manual_review_top_nodes:
            issues.append({
                "type": "extra_top_node",
                "node": node,
                "severity": "blocking" if block_extra_top_nodes else "warning",
            })

        result = {
            "passed": passed,
            "code": "" if passed else INVALID_CODE,
            "phase": phase,
            "required_root": required_root,
            "required_root_exists": root_exists,
            "cache_mesh_count": cache_mesh_count,
            "extra_top_nodes": extra_top_nodes,
            "safe_delete_top_nodes": safe_delete_top_nodes,
            "manual_review_top_nodes": manual_review_top_nodes,
            "block_extra_top_nodes": block_extra_top_nodes,
            "legacy_geo_roots": legacy_geo_roots,
            "candidate_source_roots": candidate_source_roots,
            "active_rig_root": active_rig_root,
            "active_rig_root_exists": active_rig_root_exists,
            "active_rig_mesh_count": active_rig_mesh_count,
            "issues": issues,
        }

        if passed:
            status = "SUCCESS"
            if phase == "pre_sync":
                action = f"预同步层级检查通过，旧绑定几何根 {active_rig_root} 下 {active_rig_mesh_count} 个 mesh"
            else:
                action = f"资产层级检查通过，标准根 {required_root} 下 {cache_mesh_count} 个 mesh"
            error = ""
        else:
            status = "AUDIT_FAILED" if block_on_fail else "SUCCESS"
            action = f"资产层级检查未通过：{_format_failure(result)}"
            error = action if block_on_fail else ""

        report_output = {
            "passed": passed,
            "code": "" if passed else INVALID_CODE,
            "issue_count": len(issues),
            "required_root": required_root,
            "active_rig_root": active_rig_root,
            "cache_mesh_count": cache_mesh_count,
            "active_rig_mesh_count": active_rig_mesh_count,
            "extra_top_nodes": extra_top_nodes,
            "manual_review_top_nodes": manual_review_top_nodes,
            "legacy_geo_roots": legacy_geo_roots,
            "candidate_source_roots": candidate_source_roots,
            "issues": issues,
            "result": result,
        }

        return make_receipt(
            api_id=API_ID,
            status=status,
            start_time=t0,
            input={
                "source_path": source_path,
                "stage": stage,
                "phase": phase,
                "required_root": required_root,
                "block_on_fail": block_on_fail,
                "block_extra_top_nodes": block_extra_top_nodes,
            },
            output=report_output,
            summary_input=source_path,
            summary_action=action,
            summary_count=cache_mesh_count,
            summary_label="mesh",
            error=error,
        )
    except Exception as exc:
        return make_receipt(
            api_id=API_ID,
            status="ERROR",
            start_time=t0,
            input={
                "source_path": source_path,
                "stage": stage,
                "phase": phase,
                "block_on_fail": block_on_fail,
                "block_extra_top_nodes": block_extra_top_nodes,
            },
            output={
                "passed": False,
                "code": INVALID_CODE,
                "result": {"passed": False, "code": INVALID_CODE},
            },
            summary_input=source_path,
            summary_action="资产层级检查执行失败",
            error=f"资产层级检查执行失败: {exc}\n{traceback.format_exc()}",
        )
