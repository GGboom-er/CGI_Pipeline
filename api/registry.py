"""API Catalog loader and faceted lookup."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import yaml

from .contract import ApiContractError, validate_spec


_API_DIR = Path(__file__).parent
_api_map: dict[str, dict[str, Any]] = {}


def _load() -> None:
    loaded: dict[str, dict[str, Any]] = {}
    for manifest_path in sorted(_API_DIR.rglob("api.yaml")):
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            spec = validate_spec(raw, str(manifest_path))
        except (OSError, yaml.YAMLError, ApiContractError) as exc:
            raise ApiContractError(f"failed to load API manifest {manifest_path}: {exc}") from exc
        api_id = spec["api_id"]
        if api_id in loaded:
            raise ApiContractError(f"duplicate api_id: {api_id}")
        loaded[api_id] = spec
    global _api_map
    _api_map = loaded


def reload() -> None:
    _load()


def list_apis(
    *,
    dcc: str | None = None,
    domain: str | None = None,
    tier: str | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    """List API specs, optionally filtered by catalog facets."""
    rows = []
    for spec in _api_map.values():
        if dcc and spec["dcc"] != dcc:
            continue
        if domain and spec["domain"] != domain:
            continue
        if tier and spec["tier"] != tier:
            continue
        if action and spec["action"] != action:
            continue
        rows.append(dict(spec))
    return sorted(rows, key=lambda item: item["api_id"])


def get_api(api_id: str) -> dict[str, Any]:
    try:
        return dict(_api_map[api_id])
    except KeyError as exc:
        raise KeyError(f"unknown api_id: {api_id}") from exc


def api_help(api_id: str) -> dict[str, Any]:
    """Return the manifest-backed help contract; no duplicate prose is stored."""
    spec = get_api(api_id)
    return {key: value for key, value in spec.items() if key not in {"handler", "source"}}


def get_api_handler(api_id: str):
    spec = get_api(api_id)
    module_name, function_name = spec["handler"].split(":", 1)
    module = importlib.import_module(module_name)
    try:
        return getattr(module, function_name)
    except AttributeError as exc:
        raise ApiContractError(f"handler not found for {api_id}: {spec['handler']}") from exc


_load()
