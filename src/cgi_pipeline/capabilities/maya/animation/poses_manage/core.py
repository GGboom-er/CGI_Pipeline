"""Scene-local, bounded numeric pose capture and application."""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any

from cgi_pipeline.capabilities.maya.animation_core import (
    _cmds, _limit, _long, _mutate, _number, _receipt,
)
from cgi_pipeline.contracts import ApiContext


_DATA_ATTR = "cgiPoseData"
_SCHEMA = "cgi-pose-v1"
_MAX_TARGETS = 65536
_MAX_POSE_NODES = 10000
_DEFAULT_ATTRIBUTES = [
    "translateX", "translateY", "translateZ",
    "rotateX", "rotateY", "rotateZ",
    "scaleX", "scaleY", "scaleZ",
]
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_]+")


def _pose_nodes(cmds) -> list[str]:
    network_nodes = list(cmds.ls(type="network", long=True) or [])
    if len(network_nodes) > _MAX_POSE_NODES:
        raise ValueError(f"network node scan exceeds hard limit {_MAX_POSE_NODES}")
    return sorted(
        node for node in network_nodes if cmds.objExists(f"{node}.{_DATA_ATTR}")
    )


def _record(cmds, node: str) -> dict[str, Any]:
    raw = cmds.getAttr(f"{node}.{_DATA_ATTR}") or ""
    try:
        record = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"Invalid pose data on {node}: {exc}") from exc
    if not isinstance(record, dict) or record.get("schema") != _SCHEMA:
        raise ValueError(f"Unsupported pose schema on: {node}")
    if not isinstance(record.get("name"), str) or not record["name"]:
        raise ValueError(f"Pose name is missing on: {node}")
    if not isinstance(record.get("values"), list):
        raise ValueError(f"Pose values are invalid on: {node}")
    return record


def _named_pose(cmds, name: Any, *, required: bool = True):
    if not isinstance(name, str) or not name or len(name) > 128:
        raise ValueError("name must be a non-empty string up to 128 characters")
    matches = []
    for node in _pose_nodes(cmds):
        record = _record(cmds, node)
        if record["name"] == name:
            matches.append((node, record))
    if not matches:
        if required:
            raise ValueError(f"Pose does not exist: {name}")
        return None
    if len(matches) > 1:
        raise ValueError(f"Pose name is ambiguous: {name}")
    return matches[0]


def _attributes(params: dict[str, Any], maximum: int) -> list[str]:
    values = params.get("attributes") or list(_DEFAULT_ATTRIBUTES)
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        raise ValueError("attributes must be a non-empty array within max_items")
    if not all(
        isinstance(value, str) and value and "." not in value
        for value in values
    ):
        raise ValueError("attributes must contain relative attribute names")
    if len(set(values)) != len(values):
        raise ValueError("attributes must be unique")
    return values


def _pose_value(value: Any, label: str) -> bool | float:
    if isinstance(value, bool):
        return value
    return _number(value, label)


def _capture(cmds, params: dict[str, Any], maximum: int) -> list[dict[str, Any]]:
    raw_nodes = params.get("nodes") or []
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= maximum:
        raise ValueError("nodes must be a non-empty array within max_items")
    nodes = [_long(cmds, node) for node in raw_nodes]
    if len(set(nodes)) != len(nodes):
        raise ValueError("nodes must be unique")
    attributes = _attributes(params, maximum)
    if len(nodes) * len(attributes) > _MAX_TARGETS:
        raise ValueError(f"nodes times attributes must not exceed {_MAX_TARGETS}")
    rows = []
    for node in nodes:
        uuid = str((cmds.ls(node, uuid=True) or [""])[0])
        for attribute in attributes:
            plug = f"{node}.{attribute}"
            if not cmds.objExists(plug):
                raise ValueError(f"Attribute does not exist: {plug}")
            value = _pose_value(cmds.getAttr(plug), plug)
            rows.append({
                "node": node, "uuid": uuid,
                "attribute": attribute, "value": value,
            })
    return rows


def _mapped_rows(cmds, record: dict[str, Any], params: dict[str, Any],
                 *, mirrored: bool) -> list[dict[str, Any]]:
    maximum = _limit(params)
    saved_values = record["values"]
    if len(saved_values) > min(_MAX_TARGETS, maximum * maximum):
        raise ValueError("pose value count exceeds the bounded target limit")
    raw_map = params.get("node_map") or {}
    if not isinstance(raw_map, dict):
        raise ValueError("node_map must be an object")
    if not all(
        isinstance(source, str) and source
        and isinstance(target, str) and target
        for source, target in raw_map.items()
    ):
        raise ValueError("node_map keys and values must be non-empty strings")
    if mirrored and not raw_map:
        raise ValueError("mirror requires an explicit node_map")
    raw_multipliers = params.get("attribute_multipliers") or {}
    if not isinstance(raw_multipliers, dict):
        raise ValueError("attribute_multipliers must be an object")
    if mirrored and not raw_multipliers:
        raise ValueError("mirror requires explicit attribute_multipliers")
    multipliers = {
        attribute: _number(value, f"attribute_multipliers.{attribute}")
        for attribute, value in raw_multipliers.items()
        if isinstance(attribute, str) and attribute
    }
    if len(multipliers) != len(raw_multipliers):
        raise ValueError("attribute_multipliers keys must be non-empty strings")
    rows = []
    target_plugs = set()
    for saved in saved_values:
        if not isinstance(saved, dict):
            raise ValueError("Pose contains a non-object value row")
        source_node = saved.get("node")
        attribute = saved.get("attribute")
        if not isinstance(source_node, str) or not isinstance(attribute, str):
            raise ValueError("Pose contains invalid node or attribute data")
        if mirrored and source_node not in raw_map:
            raise ValueError(f"mirror node_map is missing: {source_node}")
        mapped = raw_map.get(source_node, source_node)
        if not isinstance(mapped, str) or not mapped:
            raise ValueError(f"Invalid mapped node for: {source_node}")
        node = _long(cmds, mapped)
        plug = f"{node}.{attribute}"
        if plug in target_plugs:
            raise ValueError(f"Pose maps more than once to target: {plug}")
        target_plugs.add(plug)
        if not cmds.objExists(plug):
            raise ValueError(f"Attribute does not exist: {plug}")
        if bool(cmds.getAttr(plug, lock=True)):
            raise ValueError(f"Attribute is locked: {plug}")
        if not bool(cmds.getAttr(plug, settable=True)):
            raise ValueError(f"Attribute is not settable: {plug}")
        value = _pose_value(saved.get("value"), plug)
        multiplier = multipliers.get(attribute, 1.0)
        if isinstance(value, bool) and multiplier != 1.0:
            raise ValueError(f"Boolean pose value cannot be mirrored: {plug}")
        value = value if isinstance(value, bool) else value * multiplier
        value = _pose_value(value, plug)
        before = _pose_value(cmds.getAttr(plug), plug)
        rows.append({
            "source_node": source_node, "node": node,
            "attribute": attribute, "plug": plug,
            "before": before, "value": value, "multiplier": multiplier,
        })
    return rows


def _apply(cmds, rows: list[dict[str, Any]], chunk_name: str) -> list[dict[str, Any]]:
    def apply():
        for row in rows:
            cmds.setAttr(row["plug"], row["value"])
        for row in rows:
            actual = _pose_value(cmds.getAttr(row["plug"]), row["plug"])
            expected = row["value"]
            if isinstance(expected, bool):
                matched = actual is expected
            else:
                matched = math.isclose(
                    float(actual), expected, rel_tol=1e-7, abs_tol=1e-9
                )
            if not matched:
                raise RuntimeError(f"Pose readback mismatch: {row['plug']}")
            row["after"] = actual
        return rows

    return _mutate(cmds, chunk_name, apply)


def execute(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    cmds = _cmds(context)

    def run():
        action = params.get("action")
        maximum = _limit(params)
        if action == "list":
            nodes = _pose_nodes(cmds)
            visible = [(node, _record(cmds, node)) for node in nodes[:maximum]]
            return {
                "action": action,
                "items": [{"pose_node": node, "name": record["name"],
                           "value_count": len(record["values"])}
                          for node, record in visible],
                "count": len(nodes), "returned_count": len(visible),
                "truncated": len(nodes) > len(visible),
            }
        if action == "save":
            name = params.get("name")
            if not isinstance(name, str) or not name or len(name) > 128:
                raise ValueError("name must be a non-empty string up to 128 characters")
            overwrite = params.get("overwrite", False)
            if not isinstance(overwrite, bool):
                raise ValueError("overwrite must be boolean")
            values = _capture(cmds, params, maximum)
            match = _named_pose(cmds, name, required=False)
            existing = match[0] if match else None
            if existing and not overwrite:
                raise ValueError(f"Pose already exists: {name}")
            record = {"schema": _SCHEMA, "name": name, "values": values}
            encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))

            def save():
                pose_node = existing
                if not pose_node:
                    suffix = _SAFE_NAME.sub("_", name).strip("_") or "pose"
                    pose_node = cmds.createNode("network", name=f"CGIPose_{suffix}")
                    cmds.addAttr(pose_node, longName=_DATA_ATTR, dataType="string")
                cmds.setAttr(f"{pose_node}.{_DATA_ATTR}", encoded, type="string")
                if _record(cmds, pose_node) != record:
                    raise RuntimeError("Pose save readback mismatch")
                return {"action": action, "pose_node": pose_node,
                        "name": name, "value_count": len(values),
                        "overwritten": bool(existing)}

            return _mutate(cmds, "cgi_animation_pose_save", save)
        if action not in {"load", "mirror"}:
            raise ValueError(f"Unsupported pose action: {action}")
        pose_node, record = _named_pose(cmds, params.get("name"))
        rows = _mapped_rows(cmds, record, params, mirrored=action == "mirror")
        rows = _apply(cmds, rows, f"cgi_animation_pose_{action}")
        return {"action": action, "pose_node": pose_node,
                "name": record["name"], "items": rows, "count": len(rows)}

    return _receipt(context, "maya.animation.poses.manage", started, params, run)
