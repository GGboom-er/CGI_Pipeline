"""Read the generated capability catalog without parsing YAML at runtime."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Iterable

from .contracts import ApiContractError, validate_spec


_CATALOG_PATH = Path(__file__).parent / "data" / "capabilities.json"
_capability_map: dict[str, dict[str, Any]] = {}


def reload() -> None:
    try:
        rows = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiContractError(f"failed to load capability catalog: {exc}") from exc
    loaded: dict[str, dict[str, Any]] = {}
    for raw in rows:
        spec = validate_spec(raw, str(_CATALOG_PATH))
        api_id = spec["api_id"]
        if api_id in loaded:
            raise ApiContractError(f"duplicate api_id: {api_id}")
        loaded[api_id] = spec
    global _capability_map
    _capability_map = loaded


def _matches_any(values: Iterable[str], requested: Iterable[str] | None) -> bool:
    wanted = set(requested or [])
    return not wanted or bool(set(values) & wanted)


def list_capabilities(*, executor: str | None = None, capability: str | None = None,
                      operation: str | None = None, stages: Iterable[str] | None = None,
                      targets: Iterable[str] | None = None, access: str | None = None,
                      surfaces: Iterable[str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for spec in _capability_map.values():
        if executor and spec["executor"] != executor:
            continue
        if capability and spec["capability"] != capability:
            continue
        if operation and spec["operation"] != operation:
            continue
        if access and spec["access"] != access:
            continue
        if not _matches_any(spec["stages"], stages):
            continue
        if not _matches_any(spec["targets"], targets):
            continue
        if not _matches_any(spec["surfaces"], surfaces):
            continue
        rows.append(dict(spec))
    return sorted(rows, key=lambda item: item["api_id"])


def get_capability(api_id: str) -> dict[str, Any]:
    try:
        return dict(_capability_map[api_id])
    except KeyError as exc:
        raise KeyError(f"unknown api_id: {api_id}") from exc


def capability_help(api_id: str) -> dict[str, Any]:
    spec = get_capability(api_id)
    return {key: value for key, value in spec.items() if key not in {"handler", "source"}}


def load_handler(api_id: str):
    module_name, function_name = get_capability(api_id)["handler"].split(":", 1)
    module = importlib.import_module(module_name)
    try:
        return getattr(module, function_name)
    except AttributeError as exc:
        raise ApiContractError(f"handler not found for {api_id}: {module_name}:{function_name}") from exc


reload()
