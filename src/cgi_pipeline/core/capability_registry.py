"""Runtime indexes derived from the single capability catalog."""

from __future__ import annotations

from cgi_pipeline.catalog import list_capabilities, reload as reload_catalog


_capabilities: list[dict] = []
_capability_map: dict[str, dict] = {}
_executor_map: dict[str, str] = {}
_skip_audit: set[str] = set()


def _load() -> None:
    global _capabilities, _capability_map, _executor_map, _skip_audit
    _capabilities = list_capabilities()
    _capability_map = {item["api_id"]: item for item in _capabilities}
    _executor_map = {item["api_id"]: item["executor"] for item in _capabilities}
    _skip_audit = {item["api_id"] for item in _capabilities if item.get("skip_audit", False)}


def reload() -> None:
    reload_catalog()
    _load()


def get_all_capabilities() -> list[dict]:
    return list(_capabilities)


def get_capability_map() -> dict[str, dict]:
    return dict(_capability_map)


def get_executor(api_id: str) -> str:
    try:
        return _executor_map[api_id]
    except KeyError as exc:
        raise KeyError(f"unknown api_id: {api_id}") from exc


def resolve_api_id(value: str) -> str:
    if value in _capability_map:
        return value
    matches = [api_id for api_id in _capability_map if api_id.rsplit(".", 1)[-1] == value]
    if len(matches) == 1:
        return matches[0]
    raise KeyError(f"unknown or ambiguous api id: {value}")


def get_skip_audit_capabilities() -> set[str]:
    return set(_skip_audit)


_load()

