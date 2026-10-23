"""Bounded Blender scene CRUD implemented with bpy's data API."""

from __future__ import annotations

import fnmatch
import time
from typing import Any, Callable

from cgi_pipeline.contracts import ApiContext, make_api_receipt

_HARD_MAX = 2000


def _identity(context: ApiContext, fallback: str) -> tuple[str, str]:
    return (str(context.extras.get("_api_id") or fallback),
            str(context.extras.get("_api_version") or "1.0.0"))


def _bpy(context: ApiContext):
    module = context.extras.get("bpy_module")
    if module is None:
        raise RuntimeError("Blender execution context is required")
    return module


def _receipt(context: ApiContext, fallback: str, started: float,
             params: dict[str, Any], operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    api_id, version = _identity(context, fallback)
    try:
        output = operation()
        status = str(output.pop("_receipt_status", "SUCCESS"))
        failed = int(output.get("failed", 0))
        return make_api_receipt(
            api_id, version, status, started, input_data=dict(params), output=output,
            error_code=("BLENDER_SCENE_CORE_PARTIAL" if status == "PARTIAL" else
                        "BLENDER_SCENE_CORE_ITEMS_FAILED" if status == "ERROR" else ""),
            error=(f"{failed} of {output.get('count', 0)} items failed."
                   if status != "SUCCESS" else ""),
            recovery_hint=("Inspect per-item errors, correct invalid names or parameters, and retry failed items."
                           if status != "SUCCESS" else ""),
        )
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params),
            error_code="BLENDER_SCENE_CORE_OPERATION_FAILED", error=str(exc),
            recovery_hint="Verify bounded object names and operation parameters in Blender.",
        )


def _limit(params: dict[str, Any]) -> int:
    value = int(params.get("max_items", 200))
    if value < 1 or value > _HARD_MAX:
        raise ValueError(f"max_items must be between 1 and {_HARD_MAX}")
    return value


def _object(bpy, name: str):
    obj = bpy.data.objects.get(str(name))
    if obj is None:
        raise ValueError(f"Object does not exist: {name}")
    return obj


def _bounded(values: list[Any], maximum: int) -> tuple[list[Any], bool]:
    return values[:maximum], len(values) > maximum


def _rows(items: list[Any], maximum: int,
          callback: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    bounded, truncated = _bounded(items, maximum)
    rows = []
    for item in bounded:
        try:
            row = callback(item)
            row.setdefault("status", "SUCCESS")
        except Exception as exc:
            row = {"before": None, "after": None, "status": "ERROR", "error": str(exc)}
        rows.append(row)
    succeeded = sum(row.get("status") == "SUCCESS" for row in rows)
    failed = len(rows) - succeeded
    status = "ERROR" if rows and failed == len(rows) else "PARTIAL" if failed else "SUCCESS"
    return {
        "items": rows, "count": len(rows), "succeeded": succeeded,
        "failed": failed, "truncated": truncated, "_receipt_status": status,
    }


def _link_object(bpy, obj, collection_name: str = "") -> str:
    if collection_name:
        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            raise ValueError(f"Collection does not exist: {collection_name}")
    else:
        collection = bpy.context.scene.collection
    collection.objects.link(obj)
    return collection.name


def _collection(bpy, collection_name: str = ""):
    if not collection_name:
        return bpy.context.scene.collection
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise ValueError(f"Collection does not exist: {collection_name}")
    return collection


def _remove_created(bpy, obj, data, object_type: str) -> None:
    if obj is not None:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except (KeyError, ReferenceError, RuntimeError, ValueError):
            pass
    if data is None:
        return
    key = {
        "MESH": "meshes", "CURVE": "curves", "ARMATURE": "armatures",
        "CAMERA": "cameras", "LIGHT": "lights",
    }.get(object_type.upper())
    if key:
        try:
            getattr(bpy.data, key).remove(data)
        except (KeyError, ReferenceError, RuntimeError, TypeError, ValueError):
            pass


def _new_data(bpy, object_type: str, name: str):
    object_type = object_type.upper()
    if object_type == "EMPTY":
        return None
    collections = {
        "MESH": "meshes", "CURVE": "curves", "ARMATURE": "armatures",
        "CAMERA": "cameras", "LIGHT": "lights",
    }
    key = collections.get(object_type)
    if key is None:
        raise ValueError(f"Unsupported object_type: {object_type}")
    data_collection = getattr(bpy.data, key)
    if object_type == "CURVE":
        return data_collection.new(f"{name}Data", "CURVE")
    if object_type == "LIGHT":
        return data_collection.new(f"{name}Data", "POINT")
    return data_collection.new(f"{name}Data")


def _vector(value) -> list[float]:
    return [float(component) for component in value]


def _plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if hasattr(value, "to_list"):
        return [_plain(item) for item in value.to_list()]
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def _transform(obj, space: str) -> dict[str, list[float]]:
    if space == "world":
        location, rotation, scale = obj.matrix_world.decompose()
        order = obj.rotation_mode if obj.rotation_mode in {
            "XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"} else "XYZ"
        rotation = rotation.to_euler(order)
    else:
        location, rotation, scale = obj.location, obj.rotation_euler, obj.scale
    return {"translation": _vector(location), "rotation_euler": _vector(rotation),
            "scale": _vector(scale)}


def _set_transform(obj, values: dict[str, Any], space: str, context: ApiContext) -> None:
    if space == "local":
        if "translation" in values:
            obj.location = values["translation"]
        if "rotation_euler" in values:
            obj.rotation_euler = values["rotation_euler"]
        if "scale" in values:
            obj.scale = values["scale"]
        return
    mathutils = context.extras.get("mathutils_module")
    if mathutils is None:
        import mathutils
    current = _transform(obj, "world")
    location = values.get("translation", current["translation"])
    rotation = values.get("rotation_euler", current["rotation_euler"])
    scale = values.get("scale", current["scale"])
    order = obj.rotation_mode if obj.rotation_mode in {
        "XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"} else "XYZ"
    quaternion = mathutils.Euler(rotation, order).to_quaternion()
    obj.matrix_world = mathutils.Matrix.LocRotScale(
        mathutils.Vector(location), quaternion, mathutils.Vector(scale))


def find_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        patterns = params.get("patterns") or ["*"]; object_type = params.get("node_type") or ""
        found = [obj for obj in bpy.data.objects
                 if (not object_type or obj.type == object_type.upper())
                 and any(fnmatch.fnmatchcase(obj.name, pattern) for pattern in patterns)]
        found.sort(key=lambda obj: obj.name)
        bounded, truncated = _bounded(found, _limit(params))
        return {"items": [{"name": obj.name, "type": obj.type, "status": "SUCCESS"}
                          for obj in bounded], "count": len(bounded), "truncated": truncated}
    return _receipt(context, "blender.scene.nodes.find", started, params, run)


def inspect_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def inspect(name):
            obj = _object(bpy, name)
            return {"name": obj.name, "type": obj.type,
                    "parent": obj.parent.name if obj.parent else None,
                    "collections": [collection.name for collection in obj.users_collection],
                    "visible": not obj.hide_viewport}
        return _rows(params["nodes"], _limit(params), inspect)
    return _receipt(context, "blender.scene.nodes.inspect", started, params, run)


def create_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def create(item):
            name = item.get("name") or item["object_type"].title()
            collection = _collection(bpy, item.get("collection", ""))
            parent = _object(bpy, item["parent"]) if item.get("parent") else None
            data = obj = None
            try:
                data = _new_data(bpy, item["object_type"], name)
                obj = bpy.data.objects.new(name, data)
                collection.objects.link(obj)
                if parent is not None:
                    obj.parent = parent
            except Exception:
                _remove_created(bpy, obj, data, item["object_type"])
                raise
            return {"before": None, "after": obj.name, "type": obj.type,
                    "collection": collection.name}
        return _rows(params["items"], _limit(params), create)
    return _receipt(context, "blender.scene.nodes.create", started, params, run)


def rename_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def rename(item):
            obj = _object(bpy, item["node"]); before = obj.name
            obj.name = item["new_name"]
            return {"before": before, "after": obj.name}
        return _rows(params["items"], _limit(params), rename)
    return _receipt(context, "blender.scene.nodes.rename", started, params, run)


def delete_nodes(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def delete(name):
            obj = _object(bpy, name); before = obj.name
            bpy.data.objects.remove(obj, do_unlink=True)
            return {"before": before, "after": None}
        return _rows(params["nodes"], _limit(params), delete)
    return _receipt(context, "blender.scene.nodes.delete", started, params, run)


def duplicate_nodes_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def duplicate(item):
            source = _object(bpy, item["node"])
            collection_name = item.get("collection", "")
            collection = (_collection(bpy, collection_name) if collection_name else
                          source.users_collection[0] if source.users_collection else
                          bpy.context.scene.collection)
            copied = copied_data = None
            try:
                copied = source.copy()
                if item.get("copy_data") and source.data is not None:
                    copied_data = source.data.copy()
                    copied.data = copied_data
                if item.get("name"):
                    copied.name = item["name"]
                collection.objects.link(copied)
            except Exception:
                _remove_created(bpy, copied, copied_data, source.type)
                raise
            return {"before": source.name, "after": copied.name, "collection": collection.name}
        return _rows(params["items"], _limit(params), duplicate)
    return _receipt(context, "blender.scene.nodes.duplicate_many", started, params, run)


def inspect_hierarchy(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def inspect(name):
            obj = _object(bpy, name)
            children, truncated = _bounded(sorted(child.name for child in obj.children), _limit(params))
            return {"name": obj.name, "parent": obj.parent.name if obj.parent else None,
                    "children": children, "truncated": truncated}
        return _rows(params["nodes"], _limit(params), inspect)
    return _receipt(context, "blender.scene.hierarchy.inspect", started, params, run)


def edit_hierarchy_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def edit(item):
            obj = _object(bpy, item["node"]); before = obj.parent.name if obj.parent else None
            action = item["action"]
            world_matrix = obj.matrix_world.copy() if item.get("preserve_world", True) else None
            if action == "parent":
                parent = _object(bpy, item.get("parent", ""))
                if parent == obj:
                    raise ValueError("Object cannot parent itself")
                obj.parent = parent
            elif action == "unparent":
                obj.parent = None
            else:
                raise ValueError(f"Unsupported hierarchy action: {action}")
            if world_matrix is not None:
                obj.matrix_world = world_matrix
            return {"node": obj.name, "before": before,
                    "after": obj.parent.name if obj.parent else None, "action": action}
        return _rows(params["items"], _limit(params), edit)
    return _receipt(context, "blender.scene.hierarchy.edit_many", started, params, run)


def _read_property(obj, name: str):
    if name.startswith("custom:"):
        key = name.split(":", 1)[1]
        if key not in obj:
            raise ValueError(f"Custom property does not exist: {key}")
        return obj[key]
    if not hasattr(obj, name):
        raise ValueError(f"Object property does not exist: {name}")
    return getattr(obj, name)


def get_properties(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def get(item):
            obj = _object(bpy, item["node"]); value = _read_property(obj, item["property"])
            return {"node": obj.name, "property": item["property"], "value": _plain(value)}
        return _rows(params["items"], _limit(params), get)
    return _receipt(context, "blender.scene.properties.get", started, params, run)


def set_properties_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def set_value(item):
            obj = _object(bpy, item["node"]); name = item["property"]
            try:
                before = _read_property(obj, name)
            except ValueError:
                if not name.startswith("custom:"):
                    raise
                before = None
            if name.startswith("custom:"):
                obj[name.split(":", 1)[1]] = item["value"]
            else:
                setattr(obj, name, item["value"])
            return {"node": obj.name, "property": name, "before": _plain(before),
                    "after": _plain(_read_property(obj, name))}
        return _rows(params["items"], _limit(params), set_value)
    return _receipt(context, "blender.scene.properties.set_many", started, params, run)


def get_selection(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        selected = sorted(obj.name for obj in bpy.context.selected_objects)
        bounded, truncated = _bounded(selected, _limit(params))
        active = getattr(bpy.context.view_layer.objects, "active", None)
        return {"items": bounded, "active": active.name if active else None,
                "count": len(bounded), "truncated": truncated}
    return _receipt(context, "blender.scene.selection.get", started, params, run)


def set_selection(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        maximum = _limit(params); names, truncated = _bounded(params.get("nodes") or [], maximum)
        nodes = [_object(bpy, name) for name in names]
        before = sorted(obj.name for obj in bpy.context.selected_objects)
        mode = params.get("mode", "replace")
        if mode in {"clear", "replace"}:
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
        if mode in {"replace", "add"}:
            for obj in nodes:
                obj.select_set(True)
        elif mode == "remove":
            for obj in nodes:
                obj.select_set(False)
        elif mode != "clear":
            raise ValueError(f"Unsupported selection mode: {mode}")
        if params.get("active"):
            bpy.context.view_layer.objects.active = _object(bpy, params["active"])
        after = sorted(obj.name for obj in bpy.context.selected_objects)
        return {"before": before, "after": after, "status": "SUCCESS", "truncated": truncated}
    return _receipt(context, "blender.scene.selection.set", started, params, run)


def get_transforms(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        space = params.get("space", "world")
        def get(name):
            obj = _object(bpy, name)
            return {"name": obj.name, "space": space, **_transform(obj, space)}
        return _rows(params["nodes"], _limit(params), get)
    return _receipt(context, "blender.scene.transforms.get", started, params, run)


def set_transforms_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        space = params.get("space", "world")
        def set_value(item):
            obj = _object(bpy, item["node"]); before = _transform(obj, space)
            _set_transform(obj, item, space, context)
            return {"name": obj.name, "before": before, "after": _transform(obj, space), "space": space}
        return _rows(params["items"], _limit(params), set_value)
    return _receipt(context, "blender.scene.transforms.set_many", started, params, run)


def _node_tree(bpy, item: dict[str, Any]):
    owner_type = item.get("owner_type", "node_group")
    owner = item["owner"]
    if owner_type == "material":
        block = bpy.data.materials.get(owner)
        tree = block.node_tree if block else None
    elif owner_type == "world":
        block = bpy.data.worlds.get(owner)
        tree = block.node_tree if block else None
    elif owner_type == "node_group":
        tree = bpy.data.node_groups.get(owner)
    else:
        raise ValueError(f"Unsupported owner_type: {owner_type}")
    if tree is None:
        raise ValueError(f"Node tree does not exist: {owner_type}:{owner}")
    return tree


def inspect_node_links_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); bpy = _bpy(context)
    def run():
        def inspect(item):
            tree = _node_tree(bpy, item)
            links = [{"from_node": link.from_node.name, "from_socket": link.from_socket.name,
                      "to_node": link.to_node.name, "to_socket": link.to_socket.name}
                     for link in tree.links]
            bounded, truncated = _bounded(links, _limit(params))
            return {"owner_type": item.get("owner_type", "node_group"),
                    "owner": item["owner"], "links": bounded, "truncated": truncated}
        return _rows(params["items"], _limit(params), inspect)
    return _receipt(context, "blender.scene.node_links.inspect_many", started, params, run)
