import time
import traceback

import maya.cmds as cmds

from core.receipt import make_receipt


SKILL_ID = "maya_fix_asset_hierarchy"
DEFAULT_CAMERA_TRANSFORMS = {"|persp", "|top", "|front", "|side"}
MAX_DETAIL_ITEMS = 20


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


def _trim(values):
    values = list(values or [])
    if len(values) <= MAX_DETAIL_ITEMS:
        return values
    return values[:MAX_DETAIL_ITEMS] + [f"... 其他 {len(values) - MAX_DETAIL_ITEMS} 个"]


def _long_path(node):
    matches = cmds.ls(node, long=True) or []
    return matches[0] if matches else node


def _node_type(node):
    return cmds.nodeType(node) if cmds.objExists(node) else ""


def _path_parts(path):
    return [part for part in str(path).split("|") if part]


def _path_parent(path):
    parts = _path_parts(path)
    if len(parts) <= 1:
        return None
    return "|" + "|".join(parts[:-1])


def _short_name(path):
    return str(path).split("|")[-1].split(":")[-1]


def _top_node(path):
    parts = _path_parts(path)
    return f"|{parts[0]}" if parts else ""


def _ancestor_paths(path):
    parts = _path_parts(path)
    out = []
    for index in range(1, len(parts) + 1):
        out.append("|" + "|".join(parts[:index]))
    return out


def _unlock_nodes(nodes):
    unlocked = []
    for node in nodes or []:
        if not node or not cmds.objExists(node):
            continue
        try:
            locked = bool((cmds.lockNode(node, query=True, lock=True) or [False])[0])
        except Exception:
            continue
        if not locked:
            continue
        cmds.lockNode(node, lock=False)
        unlocked.append(_long_path(node))
    return unlocked


def _unlock_path(path):
    return _unlock_nodes(_ancestor_paths(path))


def _unlock_hierarchy(root):
    nodes = [root]
    nodes.extend(cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])
    return _unlock_nodes(nodes)


def _ensure_transform_path(required_root):
    created = []
    current = ""
    for part in _path_parts(required_root):
        candidate = (current + "|" + part) if current else "|" + part
        if cmds.objExists(candidate):
            if _node_type(candidate) != "transform":
                raise TypeError(f"标准层级节点不是 transform: {candidate}")
            current = _long_path(candidate)
            _unlock_path(current)
            continue

        parent = _path_parent(candidate)
        if parent and not cmds.objExists(parent):
            raise RuntimeError(f"标准层级父节点不存在: {parent}")
        if parent:
            _unlock_path(parent)
            made = cmds.group(empty=True, name=part, parent=parent)
        else:
            made = cmds.group(empty=True, name=part)
        current = _long_path(made)
        created.append(current)
    return _long_path(required_root), created


def _ensure_child(parent, name):
    target = f"{parent}|{name}" if parent else f"|{name}"
    if cmds.objExists(target):
        if _node_type(target) != "transform":
            raise TypeError(f"层级节点不是 transform: {target}")
        _unlock_path(target)
        return _long_path(target), []
    _unlock_path(parent)
    made = cmds.group(empty=True, name=name, parent=parent)
    return _long_path(made), [_long_path(made)]


def _unique_child_name(parent, base_name, current_node=None):
    target = f"{parent}|{base_name}"
    if not cmds.objExists(target):
        return base_name
    if current_node and _long_path(target) == _long_path(current_node):
        return base_name
    index = 1
    while True:
        candidate = f"{base_name}_{index}"
        target = f"{parent}|{candidate}"
        if not cmds.objExists(target):
            return candidate
        index += 1


def _rename_top_to_group_if_needed(legacy_root):
    top = _top_node(legacy_root)
    if not top or top == "|Group":
        return legacy_root, "", ""
    if cmds.objExists("|Group"):
        return legacy_root, top, ""

    _unlock_hierarchy(top)
    renamed = cmds.rename(top, "Group")
    group_top = _long_path(renamed)
    parts = _path_parts(legacy_root)
    new_legacy = "|Group"
    if len(parts) > 1:
        new_legacy += "|" + "|".join(parts[1:])
    return _long_path(new_legacy), top, group_top


def _normalize_legacy_geo_roots(legacy_roots):
    normalized = []
    created = []
    renamed_tops = []
    preserved_tops = []

    for raw_root in legacy_roots or []:
        if not raw_root or not cmds.objExists(raw_root):
            continue
        legacy_root = _long_path(raw_root)
        if _short_name(legacy_root) != "geo" and not _short_name(legacy_root).startswith("RIG_geo"):
            continue

        legacy_root, old_top, new_top = _rename_top_to_group_if_needed(legacy_root)
        if new_top:
            renamed_tops.append({"from": old_top, "to": new_top})
        elif old_top:
            preserved_tops.append(old_top)

        group_root, made = _ensure_transform_path("|Group")
        created.extend(made)
        geometry_root, made = _ensure_child(group_root, "Geometry")
        created.extend(made)

        if not cmds.objExists(legacy_root):
            continue
        _unlock_hierarchy(legacy_root)
        parent_now = cmds.listRelatives(legacy_root, parent=True, fullPath=True) or []
        if not parent_now or _long_path(parent_now[0]) != geometry_root:
            legacy_root = _long_path((cmds.parent(legacy_root, geometry_root) or [legacy_root])[0])

        desired_name = "RIG_geo"
        if _short_name(legacy_root) != desired_name:
            desired_name = _unique_child_name(geometry_root, desired_name, current_node=legacy_root)
            legacy_root = _long_path(cmds.rename(legacy_root, desired_name))

        normalized.append(legacy_root)

    return normalized, created, renamed_tops, _trim(preserved_tops)


def _is_under(path, root):
    return path == root or path.startswith(root + "|")


def _direct_children(root):
    return cmds.listRelatives(root, children=True, fullPath=True) or []


def _move_children_to_root(source_root, required_root):
    moved = []
    children = _direct_children(source_root)
    if not children:
        return moved

    _unlock_path(source_root)
    _unlock_path(required_root)
    for child in children:
        _unlock_hierarchy(child)
        before = _long_path(child)
        result = cmds.parent(before, required_root)
        after = _long_path(result[0] if result else before)
        moved.append({"from": before, "to": after})
    return moved


def _delete_node(node):
    if not cmds.objExists(node):
        return False
    _unlock_hierarchy(node)
    cmds.delete(node)
    return not cmds.objExists(node)


def _remove_empty_source_roots(source_roots):
    removed = []
    for root in sorted(source_roots, key=lambda item: item.count("|"), reverse=True):
        if not cmds.objExists(root):
            continue
        if _direct_children(root):
            continue
        if _delete_node(root):
            removed.append(root)
    return removed


def _delete_extra_top_nodes(top_nodes):
    deleted = []
    for node in top_nodes or []:
        if not node or node in DEFAULT_CAMERA_TRANSFORMS or node == "|Group":
            continue
        if cmds.objExists(node) and _delete_node(node):
            deleted.append(node)
    remaining = [node for node in top_nodes or [] if node and cmds.objExists(node)]
    if remaining:
        raise RuntimeError(f"顶层散落节点删除失败: {remaining}")
    return deleted


def _safe_delete_top_nodes(check_result):
    nodes = check_result.get("safe_delete_top_nodes")
    if nodes is None:
        nodes = []
    return nodes


def _check_result(params):
    result = params.get("check_result")
    if not isinstance(result, dict):
        raise ValueError("缺少 check_result；请先运行 maya_check_asset_hierarchy，并传入 outputs.result")
    return result


def _source_roots_from_check(check_result, required_root, rename_map=None):
    roots = check_result.get("candidate_source_roots") or []
    out = []
    seen = set()
    for root in roots:
        text = _remap_renamed_top(str(root).strip(), rename_map or {})
        if not text or _is_under(text, required_root):
            continue
        if not cmds.objExists(text):
            continue
        long_name = _long_path(text)
        if long_name in seen:
            continue
        seen.add(long_name)
        out.append(long_name)
    return out


def _remap_renamed_top(path, rename_map):
    text = str(path).strip()
    top = _top_node(text)
    if top and top in rename_map:
        return rename_map[top] + text[len(top):]
    return text


def _path_set_after_rename(paths, rename_map):
    result = set()
    for path in paths or []:
        text = str(path).strip()
        if text:
            result.add(text)
            result.add(_remap_renamed_top(text, rename_map))
    return result


def _run_fix(params):
    check_result = _check_result(params)
    required_root = str(check_result.get("required_root") or "").strip()
    if not required_root.startswith("|"):
        raise ValueError("check_result.required_root 缺失或不是绝对 DAG 路径")

    normalized_roots, normalized_created, renamed_tops, preserved_tops = _normalize_legacy_geo_roots(
        check_result.get("legacy_geo_roots") or []
    )
    rename_map = {
        str(item.get("from")): str(item.get("to"))
        for item in renamed_tops
        if isinstance(item, dict) and item.get("from") and item.get("to")
    }
    required_root, created_groups = _ensure_transform_path(required_root)
    created_groups = list(normalized_created) + created_groups
    active_rig_root = normalized_roots[0] if normalized_roots else str(check_result.get("active_rig_root") or "")

    legacy_set = _path_set_after_rename(check_result.get("legacy_geo_roots") or [], rename_map)
    legacy_set.update(normalized_roots)
    source_roots = _source_roots_from_check(check_result, required_root, rename_map)
    source_roots = [
        root for root in source_roots
        if root not in legacy_set and not any(_is_under(root, legacy) for legacy in legacy_set)
    ]
    moved = []
    for source_root in source_roots:
        if not cmds.objExists(source_root):
            continue
        moved.extend(_move_children_to_root(source_root, required_root))
    if moved and not normalized_roots:
        active_rig_root = required_root

    removed_sources = []
    if _as_bool(params.get("remove_empty_source"), True):
        removed_sources = _remove_empty_source_roots(source_roots)

    deleted_top_nodes = []
    if _as_bool(params.get("delete_extra_top_nodes"), False):
        deleted_top_nodes = _delete_extra_top_nodes(_safe_delete_top_nodes(check_result))

    manual_review_top_nodes = check_result.get("manual_review_top_nodes") or []
    if not active_rig_root or not cmds.objExists(active_rig_root):
        active_rig_root = required_root if cmds.objExists(required_root) else ""

    return {
        "required_root": required_root,
        "required_root_exists": cmds.objExists(required_root),
        "active_rig_root": _long_path(active_rig_root) if active_rig_root and cmds.objExists(active_rig_root) else active_rig_root,
        "normalized_rig_root_count": len(normalized_roots),
        "normalized_rig_roots": _trim(normalized_roots),
        "renamed_top_count": len(renamed_tops),
        "renamed_tops": _trim(renamed_tops),
        "preserved_top_nodes": preserved_tops,
        "manual_review_top_nodes": _trim(manual_review_top_nodes),
        "check_passed": bool(check_result.get("passed")),
        "pre_cache_mesh_count": int(check_result.get("cache_mesh_count") or 0),
        "created_group_count": len(created_groups),
        "created_groups": _trim(created_groups),
        "source_root_count": len(source_roots),
        "source_roots": _trim(source_roots),
        "moved_child_count": len(moved),
        "moved_children": _trim(moved),
        "removed_empty_source_count": len(removed_sources),
        "removed_empty_sources": _trim(removed_sources),
        "deleted_top_node_count": len(deleted_top_nodes),
        "deleted_top_nodes": _trim(deleted_top_nodes),
    }


def execute(payload):
    t0 = time.time()
    params = payload.get("parameters", {}) or {}
    source_path = payload.get("source_path") or cmds.file(query=True, sceneName=True) or ""

    chunk_open = False
    try:
        cmds.undoInfo(openChunk=True, chunkName=SKILL_ID)
        chunk_open = True
        result = _run_fix(params)
        cmds.undoInfo(closeChunk=True)
        chunk_open = False

        action = (
            f"层级修复完成：迁移 {result['moved_child_count']} 个子节点，"
            f"删除顶层散落节点 {result['deleted_top_node_count']} 个"
        )
        return make_receipt(
            skill_id=SKILL_ID,
            status="SUCCESS",
            start_time=t0,
            input={
                "source_path": source_path,
                "check_result": params.get("check_result"),
                "remove_empty_source": _as_bool(params.get("remove_empty_source"), True),
                "delete_extra_top_nodes": _as_bool(params.get("delete_extra_top_nodes"), False),
            },
            output={
                "scene": "current_maya_scene",
                "active_rig_root": result.get("active_rig_root", ""),
                "required_root": result.get("required_root", ""),
                "renamed_top_count": result.get("renamed_top_count", 0),
                "created_group_count": result.get("created_group_count", 0),
                "deleted_top_node_count": result.get("deleted_top_node_count", 0),
                "moved_child_count": result.get("moved_child_count", 0),
                "renamed_tops": result.get("renamed_tops") or [],
                "normalized_rig_roots": result.get("normalized_rig_roots") or [],
                "created_groups": result.get("created_groups") or [],
                "moved_children": result.get("moved_children") or [],
                "deleted_top_nodes": result.get("deleted_top_nodes") or [],
                "removed_empty_sources": result.get("removed_empty_sources") or [],
                "preserved_top_nodes": result.get("preserved_top_nodes") or [],
                "manual_review_top_nodes": result.get("manual_review_top_nodes") or [],
                "result": result,
            },
            summary_input=source_path,
            summary_action=action,
            summary_count=result["moved_child_count"] + result["deleted_top_node_count"],
            summary_label="项",
        )
    except Exception as exc:
        rollback_error = ""
        try:
            if chunk_open:
                cmds.undoInfo(closeChunk=True)
                chunk_open = False
            cmds.undo()
        except Exception as undo_exc:
            rollback_error = f"\n回滚失败: {undo_exc}"

        return make_receipt(
            skill_id=SKILL_ID,
            status="ERROR",
            start_time=t0,
            input={
                "source_path": source_path,
                "check_result": params.get("check_result"),
                "remove_empty_source": _as_bool(params.get("remove_empty_source"), True),
                "delete_extra_top_nodes": _as_bool(params.get("delete_extra_top_nodes"), False),
            },
            output={
                "scene": "current_maya_scene",
                "rolled_back": not rollback_error,
                "result": {"required_root": "", "rolled_back": not rollback_error},
            },
            summary_input=source_path,
            summary_action="资产层级修复失败，已尝试回滚",
            error=f"资产层级修复失败: {exc}{rollback_error}\n{traceback.format_exc()}",
        )
