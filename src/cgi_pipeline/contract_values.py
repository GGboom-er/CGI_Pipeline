"""Pure value checks shared by manifest defaults, calls, and receipt boundaries."""

from __future__ import annotations

import math
from typing import Any, Mapping


_TYPE_NAMES = {
    "string", "str", "array", "list", "object", "dict", "integer", "int",
    "number", "float", "boolean", "bool",
}


def typed_choice_matches(value: Any, choices) -> bool:
    """Choice identity includes its scalar type, so True never matches 1."""
    return any(type(value) is type(choice) and value == choice for choice in choices)


def validate_json_value(value: Any, label: str, *, error_type=ValueError) -> None:
    """Accept standard JSON values and JSON-encodable tuples; fail on cycles."""
    active: set[int] = set()

    def visit(item, path):
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise error_type(f"{path} must contain finite JSON numbers")
            return
        if not isinstance(item, (dict, list, tuple)):
            raise error_type(f"{path} contains a non-JSON value: {type(item).__name__}")
        token = id(item)
        if token in active:
            raise error_type(f"{path} contains a cyclic JSON value")
        active.add(token)
        try:
            if isinstance(item, dict):
                for key, nested in item.items():
                    if not isinstance(key, str):
                        raise error_type(f"{path} JSON object keys must be strings")
                    visit(nested, f"{path}.{key}")
            else:
                for index, nested in enumerate(item):
                    visit(nested, f"{path}[{index}]")
        finally:
            active.remove(token)

    visit(value, label)


def input_types(definition: Mapping[str, Any], name: str, *, error_type=ValueError):
    expected = definition.get("type")
    kinds = expected if isinstance(expected, (list, tuple)) else [expected]
    if not kinds or any(not isinstance(kind, str) or kind not in _TYPE_NAMES for kind in kinds):
        raise error_type(f"invalid input type for {name}: {expected!r}")
    return kinds


def validate_input_value(name: str, definition: Mapping[str, Any], value: Any,
                         *, error_type=ValueError) -> None:
    kinds = input_types(definition, name, error_type=error_type)
    valid = {
        "string": isinstance(value, str), "str": isinstance(value, str),
        "array": isinstance(value, list), "list": isinstance(value, list),
        "object": isinstance(value, dict), "dict": isinstance(value, dict),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "int": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "float": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool), "bool": isinstance(value, bool),
    }
    if not any(valid[kind] for kind in kinds):
        raise error_type(f"{name} must be a {definition['type']}")
    choices = definition.get("choices")
    if choices is not None and not typed_choice_matches(value, choices):
        raise error_type(f"invalid {name}={value!r}; choose one of {choices}")
    validate_json_value(value, name, error_type=error_type)


def validate_input_definition(name: str, definition: Mapping[str, Any],
                              *, error_type=ValueError) -> None:
    input_types(definition, name, error_type=error_type)
    if "required" in definition and type(definition["required"]) is not bool:
        raise error_type(f"required must be boolean for {name}")
    if "choices" in definition:
        choices = definition["choices"]
        if not isinstance(choices, list) or not choices:
            raise error_type(f"choices must be a non-empty list for {name}")
        plain = {key: value for key, value in definition.items() if key != "choices"}
        for choice in choices:
            validate_input_value(name, plain, choice, error_type=error_type)
    if "default" in definition:
        validate_input_value(name, definition, definition["default"], error_type=error_type)
