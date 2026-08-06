"""Runtime API catalog helpers.

The manifest catalog in :mod:`api.registry` is the only registration source.
This module keeps the small lookup helpers used by the execution runtime in
one place without maintaining a second metadata table.
"""

from __future__ import annotations

from api.registry import list_apis, reload as reload_api_catalog


_apis: list[dict] = []
_api_map: dict[str, dict] = {}
_api_dcc_map: dict[str, str] = {}
_skip_audit: set[str] = set()


def _load() -> None:
    global _apis, _api_map, _api_dcc_map, _skip_audit
    _apis = list_apis()
    _api_map = {item["api_id"]: item for item in _apis}
    _api_dcc_map = {item["api_id"]: item["dcc"] for item in _apis}
    _skip_audit = {item["api_id"] for item in _apis if item.get("skip_audit", False)}


def reload() -> None:
    reload_api_catalog()
    _load()


def get_all_apis() -> list[dict]:
    return list(_apis)


def get_api_map() -> dict[str, dict]:
    return dict(_api_map)


def get_api_dcc(api_id: str) -> str:
    try:
        return _api_dcc_map[api_id]
    except KeyError as exc:
        raise KeyError(f"unknown api_id: {api_id}") from exc


def resolve_api_id(value: str) -> str:
    """Resolve a catalog id or a short operation name to the canonical id."""
    if value in _api_map:
        return value
    matches = [api_id for api_id in _api_map if api_id.rsplit('.', 1)[-1] == value]
    if len(matches) == 1:
        return matches[0]
    raise KeyError(f"unknown or ambiguous api id: {value}")


def get_skip_audit_apis() -> set[str]:
    return set(_skip_audit)


_load()
