"""Inventory Maya MCP upstream entries from pinned checkouts.

This is a read-only extractor for the upstream review phase. It does not
import upstream code into the CGI runtime; it records source entries so each
candidate can later be reconciled to one canonical CGI API.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any, Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "integrations" / "maya" / "upstreams" / "registry.yaml"


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _candidate(
    *,
    source_id: str,
    commit: str,
    source_entry: str,
    name: str,
    group: str,
    description: str = "",
    extraction: str,
) -> dict[str, Any]:
    return {
        "candidate_id": "",
        "source_id": source_id,
        "source_commit": commit,
        "source_entry": source_entry,
        "name": name,
        "group": group or "unclassified",
        "description": " ".join(str(description).split()),
        "extraction": extraction,
        "decision": "unresolved",
        "canonical_api_id": None,
        "license": "not_reconciled",
        "reason": "",
        "implementation_status": "not_reconciled",
        "verification_status": "not_reconciled",
    }


def _decorated_tools(path: Path) -> Iterable[tuple[str, str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            text = ast.unparse(decorator)
            if ".tool" not in text:
                continue
            name = node.name
            if isinstance(decorator, ast.Call):
                for keyword in decorator.keywords:
                    if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                        name = str(keyword.value.value)
            yield name, node.name, text
            break


def _gg_titles(path: Path) -> Iterable[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.value, ast.Dict):
            continue
        target = node.target
        if not isinstance(target, ast.Name) or target.id != "TOOL_TITLES":
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                yield str(key.value), str(value.value)


def _tool_calls(path: Path) -> Iterable[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "Tool":
            continue
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                yield str(keyword.value.value)


def _top_level_functions(path: Path) -> Iterable[str]:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        for match in re.finditer(r"(?m)^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
            if not match.group(1).startswith("_"):
                yield match.group(1)
        return
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            yield node.name


def _load_registry() -> list[dict[str, Any]]:
    payload = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    return payload["repositories"]


def _extract(source_id: str, commit: str, checkout: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if source_id == "dcc_mcp_maya":
        root = checkout / "src" / "dcc_mcp_maya" / "skills"
        for path in sorted(root.rglob("tools.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            group_from_path = path.parent.name
            for tool in payload.get("tools", []):
                rows.append(_candidate(
                    source_id=source_id,
                    commit=commit,
                    source_entry=_relative(path, checkout),
                    name=str(tool.get("name", "")),
                    group=str(tool.get("group", group_from_path)),
                    description=str(tool.get("description", "")),
                    extraction="tools_yaml",
                ))
        return rows

    if source_id == "gg_mayamcp":
        path = checkout / "src" / "maya_mcp" / "tool_metadata.py"
        for name, title in _gg_titles(path):
            rows.append(_candidate(
                source_id=source_id,
                commit=commit,
                source_entry=_relative(path, checkout),
                name=name,
                group=name.split(".", 1)[0],
                description=title,
                extraction="tool_titles_ast",
            ))
        return rows

    if source_id == "chadrik_maya_mcp_server":
        paths = [checkout / "src" / "maya_mcp_server" / "server.py"]
    elif source_id == "damaya_mcp":
        paths = [checkout / "src" / "modules" / "perception.py"]
    elif source_id == "maya_mcp_server_enhanced":
        paths = sorted((checkout / "src").rglob("*.py"))
    elif source_id == "abrahamadsk_maya_mcp":
        paths = [checkout / "src" / "maya_mcp" / "server.py"]
    else:
        paths = []

    if paths:
        for path in paths:
            for name, function_name, decorator in _decorated_tools(path):
                rows.append(_candidate(
                    source_id=source_id,
                    commit=commit,
                    source_entry=f"{_relative(path, checkout)}::{function_name}",
                    name=name,
                    group="mcp_tool",
                    description=decorator,
                    extraction="decorator_ast",
                ))
        return rows

    if source_id == "aydji_autodesk_maya_mcp":
        path = checkout / "expanded_mcp_server.py"
        for name in _tool_calls(path):
            rows.append(_candidate(
                source_id=source_id,
                commit=commit,
                source_entry=_relative(path, checkout),
                name=name,
                group="mcp_tool",
                extraction="tool_call_ast",
            ))
        return rows

    if source_id == "patrickpalmer_mayamcp":
        root = checkout / "src" / "mayatools"
        for path in sorted(root.rglob("*.py")):
            if path.name == "__init__.py":
                continue
            for name in _top_level_functions(path):
                rows.append(_candidate(
                    source_id=source_id,
                    commit=commit,
                    source_entry=f"{_relative(path, checkout)}::{name}",
                    name=name,
                    group=path.parent.name,
                    extraction="dynamic_tool_module_ast",
                ))
        return rows

    raise ValueError(f"unsupported source_id: {source_id}")


def build_inventory(source_root: Path) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    repositories = _load_registry()
    for row in repositories:
        source_id = row["source_id"]
        checkout = source_root / source_id
        if not checkout.is_dir():
            raise FileNotFoundError(f"missing checkout for {source_id}: {checkout}")
        candidates.extend(_extract(source_id, row["commit"], checkout))

    for index, row in enumerate(candidates, start=1):
        row["candidate_id"] = f"MAYA-{index:03d}"

    by_source: dict[str, int] = {}
    by_extraction: dict[str, int] = {}
    for row in candidates:
        by_source[row["source_id"]] = by_source.get(row["source_id"], 0) + 1
        by_extraction[row["extraction"]] = by_extraction.get(row["extraction"], 0) + 1

    return {
        "schema_version": 1,
        "registry_path": str(REGISTRY.relative_to(ROOT)).replace("\\", "/"),
        "candidate_count": len(candidates),
        "repositories": [
            {"source_id": row["source_id"], "commit": row["commit"], "disposition": row["disposition"]}
            for row in repositories
        ],
        "summary": {"by_source": by_source, "by_extraction": by_extraction},
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = build_inventory(args.source_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_count": inventory["candidate_count"], "summary": inventory["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
