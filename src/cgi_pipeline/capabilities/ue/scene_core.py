"""Canonical bounded UE5 editor-only scene actor operations."""

from __future__ import annotations

import fnmatch
import time
from typing import Any, Callable

from cgi_pipeline.contracts import ApiContext, make_api_receipt

_HARD_MAX = 1000


def _identity(context: ApiContext, fallback: str) -> tuple[str, str]:
    return (str(context.extras.get("_api_id") or fallback),
            str(context.extras.get("_api_version") or "1.0.0"))


def _unreal(context: ApiContext):
    module = context.extras.get("unreal_module")
    if module is None:
        raise RuntimeError("Unreal Editor execution context is required")
    return module


def _subsystem(context: ApiContext, unreal, name: str):
    injected = context.extras.get("subsystems") or {}
    value = injected.get(name) if isinstance(injected, dict) else None
    value = value or context.extras.get(name)
    if value is not None:
        return value
    subsystem_type = getattr(unreal, {
        "actor_subsystem": "EditorActorSubsystem",
        "editor_subsystem": "UnrealEditorSubsystem",
    }[name])
    return unreal.get_editor_subsystem(subsystem_type)


def _receipt(context: ApiContext, fallback: str, started: float,
             params: dict[str, Any], operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    api_id, version = _identity(context, fallback)
    try:
        output = operation()
        status = str(output.pop("_receipt_status", "SUCCESS"))
        return make_api_receipt(api_id, version, status, started,
                                input_data=dict(params), output=output)
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params),
            error_code="UE_SCENE_CORE_OPERATION_FAILED", error=str(exc),
            recovery_hint="Verify actor identifiers, editor state, and bounded parameters in UE5 Editor.",
        )


def _limit(params: dict[str, Any]) -> int:
    value = int(params.get("max_items", 200))
    if value < 1 or value > _HARD_MAX:
        raise ValueError(f"max_items must be between 1 and {_HARD_MAX}")
    return value


def _bounded(values: list[Any], maximum: int) -> tuple[list[Any], bool]:
    return values[:maximum], len(values) > maximum


def _transaction(unreal, description: str):
    transaction_type = getattr(unreal, "ScopedEditorTransaction", None)
    if transaction_type is None:
        raise RuntimeError("UE5 Editor ScopedEditorTransaction is required for scene writes")
    return transaction_type(description)


def _call(value: Any, name: str, default: Any = "") -> Any:
    method = getattr(value, name, None)
    return method() if callable(method) else default


def _actor_row(actor: Any) -> dict[str, Any]:
    actor_class = _call(actor, "get_class", None)
    return {
        "path": str(_call(actor, "get_path_name", _call(actor, "get_name", ""))),
        "name": str(_call(actor, "get_name", "")),
        "label": str(_call(actor, "get_actor_label", _call(actor, "get_name", ""))),
        "class": str(_call(actor_class, "get_name", type(actor).__name__)),
    }


def _components(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if all(hasattr(value, name) for name in ("x", "y", "z")):
        return [value.x, value.y, value.z]
    if all(hasattr(value, name) for name in ("roll", "pitch", "yaw")):
        return [value.roll, value.pitch, value.yaw]
    return []


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    components = _components(value)
    return components if components else str(value)


def _all_actors(context: ApiContext, unreal) -> list[Any]:
    subsystem = _subsystem(context, unreal, "actor_subsystem")
    return list(subsystem.get_all_level_actors() or [])


def _resolve_actor(context: ApiContext, unreal, identifier: str) -> Any:
    matches = []
    for actor in _all_actors(context, unreal):
        row = _actor_row(actor)
        if identifier in {row["path"], row["name"], row["label"]}:
            matches.append(actor)
    if not matches:
        raise ValueError(f"Actor does not exist: {identifier}")
    if len(matches) > 1:
        raise ValueError(f"Actor identifier is ambiguous; use object path: {identifier}")
    return matches[0]


def _rows(items: list[Any], maximum: int, callback: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    bounded, truncated = _bounded(items, maximum)
    rows, failures = [], 0
    for item in bounded:
        try:
            row = callback(item)
            row.setdefault("status", "SUCCESS")
        except Exception as exc:
            failures += 1
            row = {"status": "ERROR", "error": str(exc), "before": None, "after": None}
        rows.append(row)
    status = "SUCCESS" if not failures else ("ERROR" if failures == len(rows) else "PARTIAL")
    return {"items": rows, "count": len(rows), "error_count": failures,
            "truncated": truncated, "_receipt_status": status}


def _vector(unreal, values: list[float] | None):
    values = values or [0.0, 0.0, 0.0]
    constructor = getattr(unreal, "Vector", None)
    return constructor(*values) if constructor else tuple(values)


def _rotator(unreal, values: list[float] | None):
    values = values or [0.0, 0.0, 0.0]
    constructor = getattr(unreal, "Rotator", None)
    return constructor(*values) if constructor else tuple(values)


def find_actors(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def run():
        patterns = params.get("patterns") or ["*"]
        class_name = str(params.get("class_name") or "")
        found = []
        for actor in _all_actors(context, unreal):
            row = _actor_row(actor)
            values = (row["path"], row["name"], row["label"])
            if any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns for value in values):
                if not class_name or row["class"] == class_name:
                    found.append({**row, "status": "SUCCESS"})
        bounded, truncated = _bounded(found, _limit(params))
        return {"items": bounded, "count": len(bounded), "truncated": truncated}
    return _receipt(context, "ue.scene.actors.find", started, params, run)


def inspect_actors(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def inspect(identifier):
        actor = _resolve_actor(context, unreal, identifier); row = _actor_row(actor)
        row["location"] = _components(_call(actor, "get_actor_location", []))
        row["rotation"] = _components(_call(actor, "get_actor_rotation", []))
        row["scale"] = _components(_call(actor, "get_actor_scale3d", []))
        return row
    return _receipt(context, "ue.scene.actors.inspect", started, params,
                    lambda: _rows(params["actors"], _limit(params), inspect))


def _actor_class(context: ApiContext, unreal, identifier: str):
    classes = context.extras.get("actor_classes") or {}
    value = classes.get(identifier) if isinstance(classes, dict) else None
    if value is None and hasattr(unreal, "load_class"):
        value = unreal.load_class(None, identifier)
    if value is None:
        raise ValueError(f"Actor class does not exist: {identifier}")
    return value


def spawn_actors(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def run():
        subsystem = _subsystem(context, unreal, "actor_subsystem")
        def spawn(item):
            actor = subsystem.spawn_actor_from_class(
                _actor_class(context, unreal, item["actor_class"]),
                _vector(unreal, item.get("location")), _rotator(unreal, item.get("rotation")))
            if item.get("label"): actor.set_actor_label(item["label"])
            return {"before": None, "after": _actor_row(actor)}
        with _transaction(unreal, "CGI: Spawn Actors"):
            return _rows(params["items"], _limit(params), spawn)
    return _receipt(context, "ue.scene.actors.spawn", started, params, run)


def rename_actors(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def run():
        def rename(item):
            actor = _resolve_actor(context, unreal, item["actor"]); before = _actor_row(actor)
            actor.modify(); actor.set_actor_label(item["new_label"])
            return {"before": before, "after": _actor_row(actor)}
        with _transaction(unreal, "CGI: Rename Actors"):
            return _rows(params["items"], _limit(params), rename)
    return _receipt(context, "ue.scene.actors.rename", started, params, run)


def delete_actors(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def run():
        subsystem = _subsystem(context, unreal, "actor_subsystem")
        def delete(identifier):
            actor = _resolve_actor(context, unreal, identifier); before = _actor_row(actor)
            actor.modify()
            if not subsystem.destroy_actor(actor): raise RuntimeError(f"Failed to delete actor: {identifier}")
            return {"before": before, "after": None}
        with _transaction(unreal, "CGI: Delete Actors"):
            return _rows(params["actors"], _limit(params), delete)
    return _receipt(context, "ue.scene.actors.delete", started, params, run)


def duplicate_actors_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def run():
        subsystem = _subsystem(context, unreal, "actor_subsystem")
        def duplicate(item):
            source = _resolve_actor(context, unreal, item["actor"])
            actor = subsystem.duplicate_actor(source)
            if item.get("label"): actor.set_actor_label(item["label"])
            return {"before": _actor_row(source), "after": _actor_row(actor)}
        with _transaction(unreal, "CGI: Duplicate Actors"):
            return _rows(params["items"], _limit(params), duplicate)
    return _receipt(context, "ue.scene.actors.duplicate_many", started, params, run)


def attach_actors_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def run():
        keep = getattr(unreal, "AttachmentRule", None)
        keep = getattr(keep, "KEEP_WORLD", None) if keep else None
        def attach(item):
            actor = _resolve_actor(context, unreal, item["actor"])
            parent = _resolve_actor(context, unreal, item["parent"])
            before = {"actor": _actor_row(actor), "parent": _actor_row(_call(actor, "get_attach_parent_actor", None)) if _call(actor, "get_attach_parent_actor", None) else None}
            actor.modify()
            result = actor.attach_to_actor(parent, item.get("socket_name", ""), keep, keep, keep, False)
            if result is False: raise RuntimeError(f"Failed to attach actor: {item['actor']}")
            return {"before": before, "after": {"actor": _actor_row(actor), "parent": _actor_row(parent)}}
        with _transaction(unreal, "CGI: Attach Actors"):
            return _rows(params["items"], _limit(params), attach)
    return _receipt(context, "ue.scene.actors.attach_many", started, params, run)


def get_properties(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def get(item):
        actor = _resolve_actor(context, unreal, item["actor"])
        values = {name: _json_value(actor.get_editor_property(name)) for name in item["properties"]}
        return {"actor": _actor_row(actor), "values": values}
    return _receipt(context, "ue.scene.properties.get", started, params,
                    lambda: _rows(params["items"], _limit(params), get))


def set_properties_many(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def run():
        def set_values(item):
            actor = _resolve_actor(context, unreal, item["actor"])
            before = {name: _json_value(actor.get_editor_property(name)) for name in item["values"]}
            actor.modify()
            for name, value in item["values"].items(): actor.set_editor_property(name, value)
            after = {name: _json_value(actor.get_editor_property(name)) for name in item["values"]}
            return {"actor": _actor_row(actor), "before": before, "after": after}
        with _transaction(unreal, "CGI: Set Actor Properties"):
            return _rows(params["items"], _limit(params), set_values)
    return _receipt(context, "ue.scene.properties.set_many", started, params, run)


def get_selection(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def run():
        subsystem = _subsystem(context, unreal, "actor_subsystem")
        rows = [{**_actor_row(actor), "status": "SUCCESS"}
                for actor in list(subsystem.get_selected_level_actors() or [])]
        bounded, truncated = _bounded(rows, _limit(params))
        return {"items": bounded, "count": len(bounded), "truncated": truncated}
    return _receipt(context, "ue.scene.selection.get", started, params, run)


def set_selection(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter(); unreal = _unreal(context)
    def run():
        maximum = _limit(params); identifiers, truncated = _bounded(params.get("actors") or [], maximum)
        subsystem = _subsystem(context, unreal, "actor_subsystem")
        before = [_actor_row(actor) for actor in list(subsystem.get_selected_level_actors() or [])]
        actors = [_resolve_actor(context, unreal, identifier) for identifier in identifiers]
        with _transaction(unreal, "CGI: Set Actor Selection"):
            subsystem.set_selected_level_actors(actors)
        after = [_actor_row(actor) for actor in list(subsystem.get_selected_level_actors() or [])]
        return {"before": before, "after": after, "status": "SUCCESS", "truncated": truncated}
    return _receipt(context, "ue.scene.selection.set", started, params, run)
