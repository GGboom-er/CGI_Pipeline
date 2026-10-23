"""Bounded expression CRUD without accepting arbitrary expression source."""

from __future__ import annotations

import re
import time
from typing import Any

from cgi_pipeline.capabilities.maya.animation_core import (
    _cmds, _limit, _long, _mutate, _number, _receipt,
)
from cgi_pipeline.contracts import ApiContext


_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_:]*$")
_UNIT_CONVERSIONS = {"all", "angularOnly", "none"}


def _expression(cmds, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("expression_node must be a non-empty string")
    matches = list(cmds.ls(value, long=True, type="expression") or [])
    if not matches:
        raise ValueError(f"Expression does not exist: {value}")
    if len(matches) > 1:
        raise ValueError(f"Expression name is ambiguous: {value}")
    return str(matches[0])


def _plug(
    cmds, value: Any, *, writable: bool, writable_expression: str = "",
) -> str:
    if not isinstance(value, str) or "." not in value:
        raise ValueError("Expression plugs must use node.attribute strings")
    node, attribute = value.rsplit(".", 1)
    if not attribute or attribute.startswith("."):
        raise ValueError(f"Invalid attribute path: {value}")
    plug = f"{_long(cmds, node)}.{attribute}"
    if not cmds.objExists(plug):
        raise ValueError(f"Attribute does not exist: {plug}")
    if writable and not bool(cmds.getAttr(plug, settable=True)):
        sources = cmds.listConnections(
            plug, source=True, destination=False, plugs=True,
            skipConversionNodes=True,
        ) or []
        source_nodes = {
            str(source).rsplit(".", 1)[0] for source in sources
            if isinstance(source, str) and "." in source
        }
        if not writable_expression or writable_expression not in source_nodes:
            raise ValueError(f"Target attribute is not settable: {plug}")
    return plug


def _statements(
    cmds, params: dict[str, Any], maximum: int, *, writable_expression: str = "",
) -> tuple[str, list[dict]]:
    raw = params.get("assignments") or []
    if not isinstance(raw, list) or not 1 <= len(raw) <= maximum:
        raise ValueError("assignments must be a non-empty array within max_items")
    rows = []
    targets = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each assignment must be an object")
        target = _plug(
            cmds, item.get("target"), writable=True,
            writable_expression=writable_expression,
        )
        if target in targets:
            raise ValueError(f"Duplicate assignment target: {target}")
        targets.add(target)
        has_source = "source" in item
        has_value = "value" in item
        if has_source == has_value:
            raise ValueError("Each assignment needs exactly one of source or value")
        if has_source:
            source = _plug(cmds, item["source"], writable=False)
            operand = source
        else:
            source = ""
            operand = repr(_number(item.get("value"), "value"))
        multiplier = _number(item.get("multiplier", 1.0), "multiplier")
        offset = _number(item.get("offset", 0.0), "offset")
        statement = f"{target} = (({operand}) * {multiplier!r}) + {offset!r};"
        rows.append({
            "target": target, "source": source,
            "value": None if source else float(operand),
            "multiplier": multiplier, "offset": offset,
            "statement": statement,
        })
    return "\n".join(row["statement"] for row in rows), rows


def _settings(params: dict[str, Any]) -> tuple[bool, str]:
    always = params.get("always_evaluate", True)
    if not isinstance(always, bool):
        raise ValueError("always_evaluate must be boolean")
    conversion = params.get("unit_conversion", "all")
    if conversion not in _UNIT_CONVERSIONS:
        raise ValueError(f"Unsupported unit_conversion: {conversion}")
    return always, str(conversion)


def _inspect(cmds, node: str) -> dict[str, Any]:
    return {
        "expression_node": node,
        "expression": str(cmds.expression(node, query=True, string=True) or ""),
        "always_evaluate": bool(
            cmds.expression(node, query=True, alwaysEvaluate=True)
        ),
        "unit_conversion": str(
            cmds.expression(node, query=True, unitConversion=True) or ""
        ),
    }


def _verify(inspected: dict[str, Any], always: bool, conversion: str) -> None:
    if not inspected["expression"].strip():
        raise RuntimeError("Expression source readback is empty")
    if inspected["always_evaluate"] is not always:
        raise RuntimeError("Expression alwaysEvaluate readback mismatch")
    if inspected["unit_conversion"] != conversion:
        raise RuntimeError("Expression unitConversion readback mismatch")


def execute(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    cmds = _cmds(context)

    def run():
        action = params.get("action")
        maximum = _limit(params)
        if action == "list":
            nodes = sorted(cmds.ls(type="expression", long=True) or [])
            visible = nodes[:maximum]
            return {
                "action": action, "items": [_inspect(cmds, node) for node in visible],
                "count": len(nodes), "returned_count": len(visible),
                "truncated": len(nodes) > len(visible),
            }
        if action == "delete":
            raw_nodes = params.get("expression_nodes") or []
            if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= maximum:
                raise ValueError("expression_nodes must be a non-empty array within max_items")
            nodes = [_expression(cmds, value) for value in raw_nodes]
            if len(set(nodes)) != len(nodes):
                raise ValueError("expression_nodes must be unique")

            def delete():
                cmds.delete(nodes)
                remaining = [node for node in nodes if cmds.objExists(node)]
                if remaining:
                    raise RuntimeError(f"Maya did not delete expression: {remaining[0]}")
                return {"action": action, "items": nodes, "count": len(nodes)}

            return _mutate(cmds, "cgi_animation_expressions_delete", delete)
        if action not in {"create", "update"}:
            raise ValueError(f"Unsupported expression action: {action}")

        update_node = (
            _expression(cmds, params.get("expression_node") or "")
            if action == "update" else ""
        )
        expression_text, assignments = _statements(
            cmds, params, maximum, writable_expression=update_node,
        )
        always, conversion = _settings(params)
        if action == "create":
            name = params.get("name") or ""
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ValueError("name must be a valid Maya dependency-node name")
            if cmds.ls(name):
                raise ValueError(f"Node already exists: {name}")

            def create():
                node = cmds.expression(
                    name=name, string=expression_text,
                    alwaysEvaluate=always, unitConversion=conversion,
                )
                node = _expression(cmds, str(node))
                inspected = _inspect(cmds, node)
                _verify(inspected, always, conversion)
                return {"action": action, "assignments": assignments, **inspected}

            return _mutate(cmds, "cgi_animation_expressions_create", create)

        node = update_node
        before = _inspect(cmds, node)

        def update():
            cmds.expression(
                node, edit=True, string=expression_text,
                alwaysEvaluate=always, unitConversion=conversion,
            )
            inspected = _inspect(cmds, node)
            _verify(inspected, always, conversion)
            return {
                "action": action, "before": before,
                "assignments": assignments, **inspected,
            }

        return _mutate(cmds, "cgi_animation_expressions_update", update)

    return _receipt(context, "maya.animation.expressions.manage", started, params, run)
