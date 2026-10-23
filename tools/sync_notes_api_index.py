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
    implementation_counts = {}
    verification_counts = {}
    for api in apis:
        access = api.get("access", "")
        counts[access] = counts.get(access, 0) + 1
        implementation = (api.get("implementation") or {}).get("status", "unknown")
        verification = (api.get("verification") or {}).get("level", "untested")
        implementation_counts[implementation] = implementation_counts.get(implementation, 0) + 1
        verification_counts[verification] = verification_counts.get(verification, 0) + 1
    api_rows = []
    for api in sorted(apis, key=lambda item: item.get("api_id", "")):
        evidence = (api.get("verification") or {}).get("evidence") or []
        api_rows.append(
            "| {api_id} | {executor} | {capability} | {operation} | {stages} | {targets} | {access} | {implementation} | {verification} | {evidence} | {surfaces} | {summary} |".format(
                api_id=api.get("api_id", ""),
                executor=api.get("executor", ""),
                capability=api.get("capability", ""),
                operation=api.get("operation", ""),
                stages=_md_list(api.get("stages") or []),
                targets=_md_list(api.get("targets") or []),
                access=api.get("access", ""),
                implementation=(api.get("implementation") or {}).get("status", "unknown"),
                verification=(api.get("verification") or {}).get("level", "untested"),
                evidence=_md_list(evidence),
                surfaces=_md_list(api.get("surfaces") or []),
                summary=str((api.get("help") or {}).get("summary", "")).replace("\n", " "),
            )
        )

    facet_rows = []
    facets = {}
    for api in apis:
        key = (api.get("executor", ""), api.get("capability", ""), api.get("operation", ""))
        facets.setdefault(key, {"stages": set(), "apis": []})
        facets[key]["stages"].update(api.get("stages") or [])
        facets[key]["apis"].append(api.get("api_id", ""))
    for (executor, capability, operation), data in sorted(facets.items()):
        facet_rows.append(
            "| `{}` | `{}` | `{}` | {} | {} |".format(
                executor, capability, operation,
                _md_list(sorted(data["stages"])),
                _md_list(sorted(data["apis"])),
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
            f"- 实现状态：implemented {implementation_counts.get('implemented', 0)} / placeholder {implementation_counts.get('placeholder', 0)} / unknown {implementation_counts.get('unknown', 0)}",
            f"- 验证状态：live_tested {verification_counts.get('live_tested', 0)} / contract_tested {verification_counts.get('contract_tested', 0)} / untested {verification_counts.get('untested', 0)}",
            "- 口径：verification 是 manifest 登记的证据级别，不是当前生产可用率；live_tested 仍须核对具体宿主、版本、输入和断言，contract_tested 只承诺登记的合同测试，untested 表示未登记验证，不证明从未运行。",
            "- 读法：旧任务的汇总 PASS 不覆盖新增或变更 API；证据缺少版本或适用范围时标未确认，不从总数推导全量通过。",
            f"- 生成命令：`python tools/sync_notes_api_index.py --write`",
            "",
            "## 使用方式",
            "",
            "1. 未知契约时用 `list_apis` 按 executor / capability / operation / stages / targets / access / effects / execution_modes / surfaces / modes 渐进筛选，search 查用途；每项只含 api_id / access / modes / 一句话 summary，默认只返回 20 项，用 cursor 继续翻页；影响、输入、实现与验证状态在 `api_help`。已知且有效的契约直接复用；旧客户端须重连取得当前工具 schema。",
            "2. 首次选择或契约过期时调用 `api_help(api_id)`；按 `mcp_call_template.requires_binding` 绑定实际数据，再使用其完整 `arguments`。已知契约执行时传 `expected_catalog_version`，不重复发现；工具 schema 与会话端口分别按客户端发现和实际会话状态确认。",
            "3. 单个动作调用 `execute_api`；同一 DCC 的一组字面原子动作调用 `execute_batch`；可复用、多步骤或跨 DCC 流程调用 `pipeline_execute_workflow`。",
            "4. 后台任务或超时任务保留 `task_id`，只用 `get_task_result` 恢复原任务，不重复提交。",
            "5. 执行前在 `api_help` 或本目录同时检查 `implementation` 与 `verification`；unknown 表示未登记实现状态，不等于无实现。先核对该行证据与当前 API 版本、宿主和输入条件；合同测试、入口烟测、场景断言、GUI 与生产验收不能互相代替。不得只凭工具数量或标签宣称可用。",
            "",
            "> 七个固定门面：`list_apis` / `api_help` / `execute_api` / `execute_batch` / `get_task_result` / `list_workflows` / `pipeline_execute_workflow`。客户端若未注入某个门面，应先刷新客户端工具清单，不改用裸 socket 或临时代码绕过。",
            "",
            "## 分层地图（机器单一来源：capabilities.json）",
            "",
            "> 选择顺序：软件 executor → 能力域 capability → 动作 operation → 流程阶段 stages → api_id。Workflow 只组合 api_id，不复制 API 契约。",
            "",
            "| 软件 | 能力域 | 动作 | 流程阶段 | API |",
            "|---|---|---|---|---|",
            *facet_rows,
            "",
            "## API 明细",
            "",
            "| api_id | executor | capability | operation | stages | targets | access | implementation | verification | 验证证据 | surfaces | 用途 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
            *api_rows,
            "",
        ]
    )


def render_project_block(apis: list[dict[str, Any]], today: str) -> str:
    counts = {}
    implementation_counts = {}
    verification_counts = {}
    for api in apis:
        access = api.get("access", "")
        counts[access] = counts.get(access, 0) + 1
        implementation = (api.get("implementation") or {}).get("status", "unknown")
        verification = (api.get("verification") or {}).get("level", "untested")
        implementation_counts[implementation] = implementation_counts.get(implementation, 0) + 1
        verification_counts[verification] = verification_counts.get(verification, 0) + 1
    return "\n".join(
        [
            START,
            "## CGI 能力摘要（自动生成）",
            "",
            f"- 更新时间：{today}",
            f"- API 总数：{len(apis)}",
            f"- 分级：read {counts.get('read', 0)} / write {counts.get('write', 0)} / destructive {counts.get('destructive', 0)}",
            f"- 实现：implemented {implementation_counts.get('implemented', 0)} / placeholder {implementation_counts.get('placeholder', 0)} / unknown {implementation_counts.get('unknown', 0)}",
            f"- 实测：live_tested {verification_counts.get('live_tested', 0)}（有真实 DCC 或服务运行 receipt）"
            f" / contract_tested {verification_counts.get('contract_tested', 0)}"
            f" / untested {verification_counts.get('untested', 0)}",
            "- 读法：本行是 manifest 登记证据的计数，不是生产覆盖率；untested 不证明从未运行，旧矩阵不覆盖新增或变更 API。按明细证据核对版本、宿主、输入和断言，范围不明标未确认。",
            "- 明细：[[cgi_pipeline_api_catalog]]",
            "- 同步：`python tools/sync_notes_api_index.py --write`",
            "- 入口：未知契约用 list_apis → api_help 发现；已知有效契约直接 execute_api 或 workflow，过期才刷新。MCP 调用按 Help 模板绑定；工具 schema 与会话状态单独确认。",
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
