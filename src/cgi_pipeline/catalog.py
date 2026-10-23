"""Read the generated capability catalog without parsing YAML at runtime.

Each public query stats the path, mtime_ns and size. Unchanged catalogs reuse
validated data; changed files reload once under a lock and publish content and
version together. Missing, invalid or concurrently changing files fail closed.
Explicit reload() forces validation even if a publisher preserved the metadata.
"""

from __future__ import annotations

import importlib
import base64
import binascii
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from .contracts import (ApiContractError, normalize_target_query,
                        validate_catalog_facets, validate_spec)


_CATALOG_PATH = Path(__file__).parent / "data" / "capabilities.json"
_capability_map: dict[str, dict[str, Any]] = {}
_catalog_version = ""
_catalog_fingerprint: tuple[str, int, int] | None = None
_catalog_lock = RLock()


def _fingerprint(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ApiContractError(f"failed to stat capability catalog: {exc}") from exc
    return str(path.absolute()), stat.st_mtime_ns, stat.st_size


def _load(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ApiContractError(f"failed to load capability catalog: {exc}") from exc
    if not isinstance(rows, list):
        raise ApiContractError("capability catalog must be a list")
    loaded: dict[str, dict[str, Any]] = {}
    for raw in rows:
        spec = validate_spec(raw, str(path))
        api_id = spec["api_id"]
        if api_id in loaded:
            raise ApiContractError(f"duplicate api_id: {api_id}")
        loaded[api_id] = spec
    canonical = json.dumps(
        [loaded[key] for key in sorted(loaded)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return loaded, hashlib.sha256(canonical).hexdigest()[:16]


def _snapshot(*, force: bool = False) -> tuple[dict[str, dict[str, Any]], str]:
    global _capability_map, _catalog_version, _catalog_fingerprint
    with _catalog_lock:
        path = _CATALOG_PATH
        fingerprint = _fingerprint(path)
        if force or fingerprint != _catalog_fingerprint:
            loaded, version = _load(path)
            if fingerprint != _fingerprint(path):
                raise ApiContractError("capability catalog changed while loading; retry the query")
            # Readers receive one validated content/version pair, even during publication.
            _capability_map, _catalog_version = loaded, version
            _catalog_fingerprint = fingerprint
        return _capability_map, _catalog_version


def reload() -> None:
    """Force a fresh read, retaining the explicit reload entry point."""
    _snapshot(force=True)


def snapshot() -> tuple[dict[str, dict[str, Any]], str]:
    """Pin validated metadata and version for one Python execution boundary.

    Treat the returned metadata as read-only. Subsequent publications replace
    the map, so an in-flight API or batch can keep its original contract.
    """
    return _snapshot()


def _matches_any(values: Iterable[str], requested: Iterable[str] | None) -> bool:
    wanted = set(requested or [])
    return not wanted or bool(set(values) & wanted)


def list_capabilities(*, executor: str | None = None, capability: str | None = None,
                      operation: str | None = None, stages: Iterable[str] | None = None,
                      targets: Iterable[str] | None = None, access: str | None = None,
                      surfaces: Iterable[str] | None = None,
                      execution_modes: Iterable[str] | None = None,
                      modes: Iterable[str] | None = None,
                      effects: Iterable[str] | None = None) -> list[dict[str, Any]]:
    specs, _ = _snapshot()
    return _filter_capabilities(
        specs, executor=executor, capability=capability, operation=operation,
        stages=stages, targets=targets, access=access, surfaces=surfaces,
        execution_modes=execution_modes, modes=modes, effects=effects,
    )


def _filter_capabilities(specs: dict[str, dict[str, Any]], *, executor=None,
                         capability=None, operation=None, stages=None,
                         targets=None, access=None, surfaces=None,
                         execution_modes=None, modes=None, effects=None) -> list[dict[str, Any]]:
    facets = {name: list(values) if values is not None else None for name, values in (
        ("stages", stages), ("targets", targets), ("surfaces", surfaces),
        ("execution_modes", execution_modes), ("modes", modes), ("effects", effects),
    )}
    facets["targets"] = normalize_target_query(facets["targets"])
    validate_catalog_facets(executor, access, **facets)
    rows = []
    for spec in specs.values():
        if executor and spec["executor"] != executor:
            continue
        if capability and spec["capability"] != capability:
            continue
        if operation and spec["operation"] != operation:
            continue
        if access and spec["access"] != access:
            continue
        if any(not _matches_any(spec[name], requested) for name, requested in facets.items()):
            continue
        rows.append(deepcopy(spec))
    return sorted(rows, key=lambda item: item["api_id"])


def catalog_version() -> str:
    """Return a stable content version for the currently loaded catalog."""
    return _snapshot()[1]


def _cursor_filter_key(filters: dict[str, Any]) -> str:
    normalized = {
        key: (sorted(set(value)) or None) if isinstance(value, (list, tuple, set)) else value
        for key, value in filters.items()
    }
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _encode_cursor(offset: int, filter_key: str, version: str) -> str:
    payload = json.dumps(
        {"version": version, "offset": offset, "filter": filter_key},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, filter_key: str, version: str) -> int:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
        offset = payload["offset"]
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("invalid list_apis cursor") from exc
    if payload.get("version") != version:
        raise ValueError("list_apis cursor is stale; restart without cursor")
    if payload.get("filter") != filter_key:
        raise ValueError("list_apis cursor does not match the current filters")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("invalid list_apis cursor offset")
    return offset


def page_capabilities(
    *,
    executor: str | None = None,
    capability: str | None = None,
    operation: str | None = None,
    stages: Iterable[str] | None = None,
    targets: Iterable[str] | None = None,
    access: str | None = None,
    surfaces: Iterable[str] | None = None,
    execution_modes: Iterable[str] | None = None,
    modes: Iterable[str] | None = None,
    effects: Iterable[str] | None = None,
    search: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Filter and page catalog rows without loading any capability handler."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("list_apis limit must be between 1 and 100")

    stage_values = list(stages) if stages is not None else None
    target_values = normalize_target_query(targets)
    surface_values = list(surfaces) if surfaces is not None else None
    execution_values = list(execution_modes) if execution_modes is not None else None
    mode_values = list(modes) if modes is not None else None
    effect_values = list(effects) if effects is not None else None
    filters = {
        "executor": executor,
        "capability": capability,
        "operation": operation,
        "stages": stage_values,
        "targets": target_values,
        "access": access,
        "surfaces": surface_values,
        "execution_modes": execution_values,
        "modes": mode_values,
        "effects": effect_values,
        "search": search or None,
    }
    specs, version = _snapshot()
    rows = _filter_capabilities(
        specs,
        executor=executor,
        capability=capability,
        operation=operation,
        stages=stage_values,
        targets=target_values,
        access=access,
        surfaces=surface_values,
        execution_modes=execution_values,
        modes=mode_values,
        effects=effect_values,
    )
    if search:
        needle = search.casefold()
        rows = [
            row for row in rows
            if needle in " ".join((
                row["api_id"],
                row["capability"],
                row["operation"],
                row.get("help", {}).get("summary", ""),
            )).casefold()
        ]

    filter_key = _cursor_filter_key(filters)
    offset = _decode_cursor(cursor, filter_key, version) if cursor else 0
    if offset > len(rows):
        raise ValueError("list_apis cursor offset is outside the filtered catalog")
    page = rows[offset:offset + limit]
    next_offset = offset + len(page)
    return {
        "total": len(rows),
        "catalog_version": version,
        "next_cursor": _encode_cursor(next_offset, filter_key, version) if next_offset < len(rows) else None,
        "apis": page,
    }


def get_capability(api_id: str) -> dict[str, Any]:
    specs, _ = _snapshot()
    try:
        return deepcopy(specs[api_id])
    except KeyError as exc:
        raise KeyError(f"unknown api_id: {api_id}") from exc


def capability_help(api_id: str) -> dict[str, Any]:
    return capability_help_with_version(api_id)[0]


def capability_help_with_version(api_id: str) -> tuple[dict[str, Any], str]:
    """Return one contract and its version from the same validated snapshot."""
    specs, version = _snapshot()
    try:
        spec = specs[api_id]
    except KeyError as exc:
        raise KeyError(f"unknown api_id: {api_id}") from exc
    return ({key: deepcopy(value) for key, value in spec.items()
             if key not in {"handler", "source"}}, version)


def load_handler(api_id: str, *, spec: dict[str, Any] | None = None):
    selected = get_capability(api_id) if spec is None else spec
    module_name, function_name = selected["handler"].split(":", 1)
    module = importlib.import_module(module_name)
    try:
        return getattr(module, function_name)
    except AttributeError as exc:
        raise ApiContractError(f"handler not found for {api_id}: {module_name}:{function_name}") from exc


reload()
