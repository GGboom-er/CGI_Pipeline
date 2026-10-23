"""Discover and open registered DCC tools without copying their source."""

from __future__ import annotations

import importlib
import importlib.util
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
    _check_version(package, host)
    roots = [(source_path / path).resolve() for path in package["python_paths"]]
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(f"package import directory does not exist: {root}")
    # Explicit roots support both package directories and directories containing packages.
    for root in reversed(roots):
        value = str(root)
        if value not in sys.path:
            sys.path.insert(0, value)
    module_name, function_name = entrypoint.split(":", 1)
    spec = importlib.util.find_spec(module_name)
    if not spec or not spec.origin:
        raise ImportError(f"package entry module not found: {module_name}")
    if not Path(spec.origin).resolve().is_relative_to(source_path):
        raise RuntimeError(f"package module loaded from another source: {module_name}: {spec.origin}")
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise TypeError(f"package entrypoint is not callable: {entrypoint}")
    return function


def _check_version(package: dict, host: str):
    versions = package.get("versions", {}).get(host)
    if not versions:
        return  # Existing packages retain their established compatibility contract.
    if host == "maya":
        from maya import cmds
        version = str(cmds.about(version=True))
    elif host == "blender":
        import bpy
        version = bpy.app.version_string
    elif host == "ue":
        import unreal
        version = unreal.SystemLibrary.get_engine_version()
    else:
        raise ValueError(f"unsupported tool host: {host}")
    if not any(version == v or version.startswith(v + ".") for v in versions):
        raise RuntimeError(f"{package['package_id']} is not validated for {host} {version}")


def open(package_id: str, *, host: str, reload: bool = False):
    """Load a registered tool; reload is explicit and never applied to binary plugins."""
    function = _load_entrypoint(package_id, host)
    package = _package(package_id)
    if not reload and not package.get("lifecycle", {}).get(host):
        return function()
    module_name, function_name = package["entrypoints"][host].split(":", 1)
    module = importlib.import_module(module_name)
    addon = package.get("lifecycle", {}).get(host) == "addon"
    if addon:
        states = [bool(getattr(cls, "is_registered", False)) for cls in module.classes]
        if not states or (any(states) and not all(states)):
            raise RuntimeError(f"{package_id} has an incomplete addon registration; restart after diagnosis")
        registered = all(states)
        if registered and not reload:
            return {"package_id": package_id, "status": "already_registered"}
        if registered:
            module.unregister()
    if reload:
        importlib.invalidate_caches()
        module = importlib.reload(module)
        function = getattr(module, function_name)
    result = function()
    if addon and not all(getattr(cls, "is_registered", False) for cls in module.classes):
        raise RuntimeError(f"{package_id} did not register all addon classes")
    return {"package_id": package_id, "status": "registered"} if addon else result


def close(package_id: str, *, host: str):
    """Unregister an addon owned by this registry; other tools retain their own UI close."""
    package = _package(package_id)
    if package.get("lifecycle", {}).get(host) != "addon":
        raise ValueError(f"{package_id} does not declare an addon lifecycle for {host}")
    _load_entrypoint(package_id, host)  # Verify version and loaded source before unregistering.
    module = importlib.import_module(package["entrypoints"][host].split(":", 1)[0])
    if any(getattr(cls, "is_registered", False) for cls in module.classes):
        module.unregister()
    return {"package_id": package_id, "status": "unregistered"}
