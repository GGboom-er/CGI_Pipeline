#!/usr/bin/env python3
"""Compile validated capability and package manifests into runtime JSON."""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE_ROOT = SRC / "cgi_pipeline"
CAPABILITIES_ROOT = PACKAGE_ROOT / "capabilities"
DATA_ROOT = PACKAGE_ROOT / "data"
PACKAGE_REGISTRY = ROOT / "packages" / "registry.yaml"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cgi_pipeline.contracts import ApiContractError, validate_spec


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ApiContractError(f"failed to load {path}: {exc}") from exc


def validate_handler(spec: dict[str, Any], source_root: Path = SRC) -> None:
    """Check static handler ownership and its two-argument contract without host imports."""
    module, _, name = spec["handler"].partition(":")
    path = source_root.joinpath(*module.split(".")).with_suffix(".py").resolve()
    owner = (source_root / "cgi_pipeline/capabilities" / spec["executor"]).resolve()
    if not path.is_relative_to(owner):
        raise ApiContractError(f"handler outside executor owner: {spec['api_id']}")
    try:
        body = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path)).body
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ApiContractError(f"handler source cannot be parsed: {path}: {exc}") from exc
    functions = [node for node in body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(functions) != 1:
        raise ApiContractError(f"handler needs one top-level function: {spec['handler']}")
    args = functions[0].args
    positional = args.posonlyargs + args.args
    required = len(positional) - len(args.defaults)
    if (required > 2 or (len(positional) < 2 and args.vararg is None)
            or any(default is None for default in args.kw_defaults)):
        raise ApiContractError(f"handler must accept (params, context): {spec['handler']}")


def build_capabilities() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(CAPABILITIES_ROOT.rglob("capability.yaml")):
        spec = validate_spec(_load_yaml(path), str(path))
        validate_handler(spec)
        api_id = spec["api_id"]
        if api_id in seen:
            raise ApiContractError(f"duplicate api_id: {api_id}")
        seen.add(api_id)
        spec.pop("source", None)
        spec["manifest_path"] = path.relative_to(ROOT).as_posix()
        rows.append(spec)
    if not rows:
        raise ApiContractError(f"no capability manifests found under {CAPABILITIES_ROOT}")
    return rows


def _validate_packages(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("packages"), list):
        raise ApiContractError("package registry must contain a packages list")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = {"package_id", "name", "category", "stages", "hosts", "source", "entrypoints"}
    for row in raw["packages"]:
        if not isinstance(row, dict):
            raise ApiContractError("package entries must be mappings")
        missing = sorted(required - set(row))
        if missing:
            raise ApiContractError(f"package entry missing {missing}")
        package_id = row["package_id"]
        if not isinstance(package_id, str) or not package_id:
            raise ApiContractError("package_id must be a non-empty string")
        if package_id in seen:
            raise ApiContractError(f"duplicate package_id: {package_id}")
        seen.add(package_id)
        if not isinstance(row["source"], dict) or row["source"].get("type") != "workspace":
            raise ApiContractError(f"unsupported package source: {package_id}")
        if not isinstance(row["entrypoints"], dict) or not row["entrypoints"]:
            raise ApiContractError(f"package entrypoints must be a non-empty mapping: {package_id}")
        paths = row.get("python_paths")
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p for p in paths):
            raise ApiContractError(f"package needs explicit python_paths: {package_id}")
        if set(row["entrypoints"]) != set(row["hosts"]):
            raise ApiContractError(f"package hosts/entrypoints differ: {package_id}")
        versions = row.get("versions", {})
        if not isinstance(versions, dict) or set(versions) - set(row["hosts"]):
            raise ApiContractError(f"invalid package version hosts: {package_id}")
        if any(not isinstance(v, list) or not v or any(not isinstance(s, str) or not s for s in v)
               for v in versions.values()):
            raise ApiContractError(f"invalid package versions: {package_id}")
        lifecycle = row.get("lifecycle", {})
        if not isinstance(lifecycle, dict) or any(h != "blender" or v != "addon" for h, v in lifecycle.items()):
            raise ApiContractError(f"unsupported package lifecycle: {package_id}")
        rows.append(dict(row))
    return sorted(rows, key=lambda item: item["package_id"])


def build_packages() -> list[dict[str, Any]]:
    return _validate_packages(_load_yaml(PACKAGE_REGISTRY))


def _json_text(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=2) + "\n"


def _write_or_check(path: Path, rows: list[dict[str, Any]], check: bool) -> bool:
    expected = _json_text(rows)
    if check:
        return path.exists() and path.read_text(encoding="utf-8") == expected
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated JSON without writing")
    args = parser.parse_args()

    outputs = {
        DATA_ROOT / "capabilities.json": build_capabilities(),
        DATA_ROOT / "packages.json": build_packages(),
    }
    stale = [path for path, rows in outputs.items() if not _write_or_check(path, rows, args.check)]
    if stale:
        for path in stale:
            print(f"STALE: {path}")
        return 1
    for path, rows in outputs.items():
        print(f"{'CHECKED' if args.check else 'WROTE'}: {path} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
