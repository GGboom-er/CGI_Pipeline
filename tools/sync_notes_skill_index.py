#!/usr/bin/env python3
"""Sync CGI Pipeline skill metadata into the Notes project layer."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NOTES_ROOT = Path("Y:/GGbommer/scripts/Notes")
START = "<!-- CGI_PROJECT_SKILL_SUMMARY:START -->"
END = "<!-- CGI_PROJECT_SKILL_SUMMARY:END -->"


def load_cli_skills() -> list[dict[str, Any]]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "cli.py", "list-skills", "--json"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)
    return payload["skills"]


def _md_list(values: list[str]) -> str:
    return ", ".join(f"`{v}`" for v in values) if values else "`[]`"


def render_catalog(skills: list[dict[str, Any]], today: str) -> str:
    counts = Counter(skill.get("tier", "") for skill in skills)
    rows = []
    for skill in sorted(skills, key=lambda item: (item.get("tier", ""), item.get("skill_id", ""))):
        rows.append(
            "| `{skill_id}` | `{dcc}` | `{tier}` | `{category}` | {pairs} | {description} |".format(
                skill_id=skill.get("skill_id", ""),
                dcc=skill.get("dcc", ""),
                tier=skill.get("tier", ""),
                category=skill.get("category", ""),
                pairs=_md_list(skill.get("pairs_with") or []),
                description=str(skill.get("description", "")).replace("\n", " "),
            )
        )

    return "\n".join(
        [
            "---",
            "file: ai/projects/cgi_pipeline_skill_catalog.md",
            "location: Y:/GGbommer/scripts/Notes/ai/projects/cgi_pipeline_skill_catalog.md",
            "status: active",
            f"updated: {today}",
            "purpose: CGI Pipeline 项目操作 skill 自动摘要；由 CGI_Pipeline/cli.py list-skills --json 生成。",
            "source: Y:/GGbommer/scripts/CGI_Pipeline/skills/*/SKILL.md",
            "---",
            "",
            "# CGI Pipeline Skill 工具目录",
            "",
            "> 自动生成；不要手写本文件。真相源是 `Y:/GGbommer/scripts/CGI_Pipeline/skills/{skill_id}/SKILL.md`。本文件是项目工具目录，不是 Notes 大脑自身 skill。",
            "",
            "## 汇总",
            "",
            f"- 总数：{len(skills)}",
            f"- read：{counts.get('read', 0)}",
            f"- write：{counts.get('write', 0)}",
            f"- destructive：{counts.get('destructive', 0)}",
            f"- 生成命令：`python tools/sync_notes_skill_index.py --write`",
            "",
            "## 明细",
            "",
            "| skill_id | dcc | tier | category | pairs_with | 用途 |",
            "|---|---|---|---|---|---|",
            *rows,
            "",
        ]
    )


def render_project_block(skills: list[dict[str, Any]], today: str) -> str:
    counts = Counter(skill.get("tier", "") for skill in skills)
    return "\n".join(
        [
            START,
            "## Skill 工具摘要（自动生成）",
            "",
            f"- 更新时间：{today}",
            f"- 总数：{len(skills)}",
            f"- 分级：read {counts.get('read', 0)} / write {counts.get('write', 0)} / destructive {counts.get('destructive', 0)}",
            "- 明细：[[cgi_pipeline_skill_catalog]]",
            "- 同步：`python tools/sync_notes_skill_index.py --write`",
            "- 分层：本摘要属于 CGI 项目工具层，不写入 `ai/skills/` 大脑能力层。",
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
    catalog_path = notes_root / "ai" / "projects" / "cgi_pipeline_skill_catalog.md"
    if catalog_path.exists():
        text = catalog_path.read_text(encoding="utf-8")
        match = re.search(r"(?m)^updated:\s*\"?([0-9-]+)\"?", text)
        if match:
            return match.group(1)
    return date.today().isoformat()


def check_notes_files(notes_root: Path, skills: list[dict[str, Any]]) -> list[str]:
    check_date = _existing_date(notes_root)
    expected_catalog = render_catalog(skills, check_date)
    expected_block = render_project_block(skills, check_date)
    issues: list[str] = []

    catalog_path = notes_root / "ai" / "projects" / "cgi_pipeline_skill_catalog.md"
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
    parser = argparse.ArgumentParser(description="Sync CGI skill metadata into Notes.")
    parser.add_argument("--notes-root", type=Path, default=DEFAULT_NOTES_ROOT)
    parser.add_argument("--write", action="store_true", help="write Notes files")
    parser.add_argument("--check", action="store_true", help="fail if Notes files are stale")
    args = parser.parse_args()

    skills = load_cli_skills()
    today = _existing_date(args.notes_root) if args.check else date.today().isoformat()
    catalog_text = render_catalog(skills, today)
    project_block = render_project_block(skills, today)

    if args.check:
        issues = check_notes_files(args.notes_root, skills)
        if issues:
            print("CGI skill summary drift:")
            for issue in issues:
                print(f"- {issue}")
            return 1
        print("CGI skill summary OK")
        return 0

    if not args.write:
        print(project_block)
        print()
        print(f"catalog rows: {len(skills)}")
        return 0

    notes_root = args.notes_root
    catalog_path = notes_root / "ai" / "projects" / "cgi_pipeline_skill_catalog.md"
    project_path = notes_root / "ai" / "projects" / "cgi_pipeline.md"
    catalog_path.write_text(catalog_text, encoding="utf-8", newline="")
    update_project_card(project_path, project_block, today)
    print(f"wrote {catalog_path}")
    print(f"updated {project_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
