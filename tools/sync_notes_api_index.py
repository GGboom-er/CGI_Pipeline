#!/usr/bin/env python3
"""Sync the CGI Pipeline API catalog into the Notes project layer."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NOTES_ROOT = Path("Y:/GGbommer/scripts/Notes")
START = "<!-- CGI_PROJECT_API_SUMMARY:START -->"
END = "<!-- CGI_PROJECT_API_SUMMARY:END -->"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def load_api_specs() -> list[dict[str, Any]]:
    """Load API metadata from the CGI API Catalog."""
    from cgi_pipeline.catalog import list_capabilities

    return list_capabilities()


def _md_list(values: list[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "`[]`"


def render_catalog(apis: list[dict[str, Any]], today: str) -> str:
    counts = {}
    for api in apis:
        access = api.get("access", "")
        counts[access] = counts.get(access, 0) + 1
    api_rows = []
    for api in sorted(apis, key=lambda item: item.get("api_id", "")):
        api_rows.append(
            "| {api_id} | {executor} | {capability} | {operation} | {stages} | {targets} | {access} | {surfaces} | {summary} |".format(
                api_id=api.get("api_id", ""),
                executor=api.get("executor", ""),
                capability=api.get("capability", ""),
                operation=api.get("operation", ""),
                stages=_md_list(api.get("stages") or []),
                targets=_md_list(api.get("targets") or []),
                access=api.get("access", ""),
                surfaces=_md_list(api.get("surfaces") or []),
                summary=str((api.get("help") or {}).get("summary", "")).replace("\n", " "),
            )
        )

    return "\n".join(
        [
            "---",
            "file: ai/projects/cgi_pipeline_api_catalog.md",
            "location: Y:/GGbommer/scripts/Notes/ai/projects/cgi_pipeline_api_catalog.md",
            "status: active",
            f"updated: {today}",
            "purpose: CGI Pipeline 项目 API 能力目录；由 API manifest 生成。",
            "source: Y:/GGbommer/scripts/Notes/Tools/_managed/cgi_pipeline/src/cgi_pipeline/capabilities/**/capability.yaml",
            "---",
            "",
            "# CGI Pipeline 能力目录",
            "",
            "> 自动生成；不要手写本文件。API manifest 是机器契约，API help 是渐进式参数说明。本文件是项目工具目录。",
            "",
            "## 汇总",
            "",
            f"- API 总数：{len(apis)}",
            f"- read：{counts.get('read', 0)}",
            f"- write：{counts.get('write', 0)}",
            f"- destructive：{counts.get('destructive', 0)}",
            f"- 生成命令：`python tools/sync_notes_api_index.py --write`",
            "",
            "## API 明细",
            "",
            "| api_id | executor | capability | operation | stages | targets | access | surfaces | 用途 |",
            "|---|---|---|---|---|---|---|---|---|",
            *api_rows,
            "",
        ]
    )


def render_project_block(apis: list[dict[str, Any]], today: str) -> str:
    counts = {}
    for api in apis:
        access = api.get("access", "")
        counts[access] = counts.get(access, 0) + 1
    return "\n".join(
        [
            START,
            "## CGI 能力摘要（自动生成）",
            "",
            f"- 更新时间：{today}",
            f"- API 总数：{len(apis)}",
            f"- 分级：read {counts.get('read', 0)} / write {counts.get('write', 0)} / destructive {counts.get('destructive', 0)}",
            "- 明细：[[cgi_pipeline_api_catalog]]",
            "- 同步：`python tools/sync_notes_api_index.py --write`",
            "- 入口：先 list_apis，再 api_help，最后 execute_api 或 workflow。",
            "",
            END,
        ]
    )


def update_project_card(project_path: Path, block: str, today: str) -> None:
    text = project_path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        marker = "\n## 6. What NOT to Do"
        if marker not in text:
            raise RuntimeError("cgi_pipeline.md missing insertion marker: ## 6. What NOT to Do")
        text = text.replace(marker, "\n" + block + "\n" + marker, 1)
    text = re.sub(r'updated:\s*"?[0-9-]+"?', f'updated: "{today}"', text, count=1)
    project_path.write_text(text, encoding="utf-8", newline="")


def _existing_date(notes_root: Path) -> str:
    catalog_path = notes_root / "ai" / "projects" / "cgi_pipeline_api_catalog.md"
    if catalog_path.exists():
        text = catalog_path.read_text(encoding="utf-8")
        match = re.search(r"(?m)^updated:\s*\"?([0-9-]+)\"?", text)
        if match:
            return match.group(1)
    return date.today().isoformat()


def check_notes_files(
    notes_root: Path,
    apis: list[dict[str, Any]],
) -> list[str]:
    check_date = _existing_date(notes_root)
    expected_catalog = render_catalog(apis, check_date)
    expected_block = render_project_block(apis, check_date)
    issues: list[str] = []

    catalog_path = notes_root / "ai" / "projects" / "cgi_pipeline_api_catalog.md"
    project_path = notes_root / "ai" / "projects" / "cgi_pipeline.md"

    if not catalog_path.exists():
        issues.append(f"missing {catalog_path}")
    elif catalog_path.read_text(encoding="utf-8") != expected_catalog:
        issues.append(f"stale {catalog_path}")

    if not project_path.exists():
        issues.append(f"missing {project_path}")
    else:
        project_text = project_path.read_text(encoding="utf-8")
        if expected_block not in project_text:
            issues.append(f"stale summary block in {project_path}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync CGI API metadata into Notes.")
    parser.add_argument("--notes-root", type=Path, default=DEFAULT_NOTES_ROOT)
    parser.add_argument("--write", action="store_true", help="write Notes files")
    parser.add_argument("--check", action="store_true", help="fail if Notes files are stale")
    args = parser.parse_args()

    apis = load_api_specs()
    today = _existing_date(args.notes_root) if args.check else date.today().isoformat()
    catalog_text = render_catalog(apis, today)
    project_block = render_project_block(apis, today)

    if args.check:
        issues = check_notes_files(args.notes_root, apis)
        if issues:
            print("CGI API catalog drift:")
            for issue in issues:
                print(f"- {issue}")
            return 1
        print("CGI API catalog OK")
        return 0

    if not args.write:
        print(project_block)
        print()
        print(f"catalog rows: {len(apis)} APIs")
        return 0

    notes_root = args.notes_root
    catalog_path = notes_root / "ai" / "projects" / "cgi_pipeline_api_catalog.md"
    project_path = notes_root / "ai" / "projects" / "cgi_pipeline.md"
    catalog_path.write_text(catalog_text, encoding="utf-8", newline="")
    update_project_card(project_path, project_block, today)
    print(f"wrote {catalog_path}")
    print(f"updated {project_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
