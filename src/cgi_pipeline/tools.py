"""Discover and open registered DCC tools without copying their source."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

from .paths import REPOSITORY_ROOT


_CATALOG_PATH = Path(__file__).parent / "data" / "packages.json"


def list(*, host: str | None = None, stage: str | None = None,
         category: str | None = None) -> list[dict[str, Any]]:
    rows = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return [
        row for row in rows
        if (not host or host in row["entrypoints"])
        and (not stage or stage in row["stages"])
        and (not category or row["category"] == category)
    ]


def _package(package_id: str) -> dict[str, Any]:
    try:
        return next(row for row in list() if row["package_id"] == package_id)
    except StopIteration as exc:
        raise KeyError(f"unknown package_id: {package_id}") from exc


def _load_entrypoint(package_id: str, host: str):
    package = _package(package_id)
    entrypoint = package["entrypoints"].get(host)
    if not entrypoint:
        raise RuntimeError(f"{package_id} has no {host} entrypoint")
    source = package["source"]
    if source["type"] != "workspace":
        raise RuntimeError(f"unsupported package source type: {source['type']}")
    source_path = (REPOSITORY_ROOT / source["path"]).resolve()
    if not source_path.is_dir():
        raise FileNotFoundError(f"package source does not exist: {source_path}")
    import_root = str(source_path.parent)
    if import_root not in sys.path:
        sys.path.insert(0, import_root)
    module_name, function_name = entrypoint.split(":", 1)
    return getattr(importlib.import_module(module_name), function_name)


def open(package_id: str, *, host: str):
    return _load_entrypoint(package_id, host)()
