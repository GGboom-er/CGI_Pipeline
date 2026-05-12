# core/compare_result_io.py
# compare_result 文件与 Markdown 摘要的公共工具。

import datetime
import json
import os

from core.asset_info_schema import validate
from core.path_guard import is_protected_path
from core.run_archive import create_run_dir


def auto_label(path):
    """从文件路径自动推断阶段标签。"""
    norm = os.path.normpath(path).replace('\\', '/').lower()
    stages = ['rig', 'tex', 'uv', 'model', 'anim', 'fx', 'cfx', 'look']
    parts = norm.split('/')
    for stage in stages:
        if stage in parts:
            return stage
    basename = os.path.basename(norm)
    for stage in stages:
        if stage in basename:
            return stage
    return os.path.splitext(os.path.basename(path))[0]


def dag_short(dag):
    """取 DAG 路径最后一段。"""
    parts = str(dag).strip("|").split("|")
    return parts[-1].split(":")[-1] if parts else dag


def _report_label(group: str, key: str) -> str:
    try:
        from core.report_labels import label
        return label(group, key)
    except Exception:
        return key


def _pair_section_item(pair: dict) -> dict:
    return {
        "source": pair.get("name_a") or dag_short(pair.get("dag_a", "")),
        "target": pair.get("name_b") or dag_short(pair.get("dag_b", "")),
        "action": pair.get("actionability", ""),
        "vertices": f"{pair.get('vtx_a', 0)} / {pair.get('vtx_b', 0)}",
        "match": pair.get("match_pct_loose", "-"),
        "offset": pair.get("max_offset", 0),
    }


def _single_section_item(entry: dict) -> dict:
    dag = entry.get("dag", "")
    return {
        "mesh": entry.get("name") or dag_short(dag),
        "dag": dag,
        "vertices": entry.get("vtx", 0),
    }


def summarize_compare_outcomes(report: dict) -> dict:
    """按用户视角汇总 compare 结果。

    算法层的 ORIG_INJECT/MODIFIED 等 actionability 保留给拼装决策；
    报告摘要只说用户关心的事实去向，避免把可注入项误报成差异。
    """
    paired = report.get("paired", []) or []
    only_source = report.get("only_a", []) or []
    only_target = report.get("only_b", []) or []
    identical = [
        p for p in paired
        if p.get("actionability") in ("IDENTICAL", "ORIG_INJECT")
    ]
    matched_different = [
        p for p in paired
        if p.get("actionability") in ("MODIFIED", "MERGE", "SPLIT")
    ]
    counts = {
        "paired": len(paired),
        "identical": len(identical),
        "matched_different": len(matched_different),
        "only_source": len(only_source),
        "only_target": len(only_target),
    }
    counts["blocking"] = (
        counts["matched_different"] +
        counts["only_source"] +
        counts["only_target"]
    )
    return counts


def format_compare_summary(report: dict) -> str:
    counts = summarize_compare_outcomes(report)
    return (
        f"{_report_label('metrics', 'paired')} {counts['paired']}，"
        f"{_report_label('metrics', 'identical')} {counts['identical']}，"
        f"{_report_label('metrics', 'matched_different')} {counts['matched_different']}，"
        f"{_report_label('metrics', 'only_source')} {counts['only_source']}，"
        f"{_report_label('metrics', 'only_target')} {counts['only_target']}"
    )


def build_compare_report_sections(report, info_source_path, info_target_path,
                                  label_source, label_target,
                                  source_file, target_file) -> list:
    """把 compare 结果转成统一报告可渲染的结构化折叠段。

    这里不写文件，也不改变 compare_result 机器契约；只服务 receipt.report_sections。
    """
    paired = report.get("paired", []) or []
    only_source = report.get("only_a", []) or []
    only_target = report.get("only_b", []) or []

    identical_items = [
        _pair_section_item(p)
        for p in paired
        if p.get("actionability") in ("IDENTICAL", "ORIG_INJECT")
    ]
    matched_different_items = [
        _pair_section_item(p)
        for p in paired
        if p.get("actionability") in ("MODIFIED", "MERGE", "SPLIT")
    ]
    only_source_items = [_single_section_item(x) for x in only_source]
    only_target_items = [_single_section_item(x) for x in only_target]

    counts = summarize_compare_outcomes(report)
    n_identical = counts["identical"]
    n_matched = counts["matched_different"]
    n_only_source = counts["only_source"]
    n_only_target = counts["only_target"]
    n_paired = counts["paired"]

    action_counts = {}
    for p in paired:
        action = p.get("actionability", "")
        if action:
            action_counts[action] = action_counts.get(action, 0) + 1
    action_counts["NEW"] = n_only_source
    action_counts["DELETE"] = n_only_target

    source_label = label_source or "source"
    target_label = label_target or "target"

    overview = [
        "| 项目 | 数量 |",
        "|---|---|",
        f"| {_report_label('metrics', 'paired')} | {n_paired} |",
        f"| {_report_label('metrics', 'identical')} | {n_identical} |",
        f"| {_report_label('metrics', 'matched_different')} | {n_matched} |",
        f"| {_report_label('metrics', 'only_source')} | {n_only_source} |",
        f"| {_report_label('metrics', 'only_target')} | {n_only_target} |",
    ]
    for key in ("MODIFIED", "MERGE", "SPLIT"):
        if action_counts.get(key):
            overview.append(f"| {_report_label('metrics', key)} | {action_counts[key]} |")

    source_lines = [
        f"- **{source_label} 源文件**: `{source_file or '-'}`",
        f"- **{source_label} 数据**: `{info_source_path or '-'}`",
        f"- **{target_label} 源文件**: `{target_file or '-'}`",
        f"- **{target_label} 数据**: `{info_target_path or '-'}`",
    ]

    sections = [
        {
            "title": "对比来源",
            "summary": f"{source_label} vs {target_label}",
            "content": "\n".join(source_lines),
        },
        {
            "title": "对比概览",
            "summary": (
                f"{_report_label('metrics', 'paired')}={n_paired} "
                f"{_report_label('metrics', 'identical')}={n_identical} "
                f"{_report_label('metrics', 'matched_different')}={n_matched} "
                f"{_report_label('metrics', 'only_source')}={n_only_source} "
                f"{_report_label('metrics', 'only_target')}={n_only_target}"
            ),
            "content": "\n".join(overview),
        },
        {
            "title": _report_label("metrics", "identical"),
            "summary": f"{n_identical} 项",
            "items": identical_items,
        },
        {
            "title": _report_label("metrics", "matched_different"),
            "summary": f"{n_matched} 项",
            "items": matched_different_items,
        },
        {
            "title": _report_label("metrics", "only_source"),
            "summary": f"{n_only_source} 项",
            "items": only_source_items,
        },
        {
            "title": _report_label("metrics", "only_target"),
            "summary": f"{n_only_target} 项",
            "items": only_target_items,
        },
    ]

    hierarchy = report.get("hierarchy") or {}
    if hierarchy:
        sections.append({
            "title": "层级匹配",
            "summary": f"名称一致 {hierarchy.get('matched', 0)} 项",
            "content": "\n".join([
                f"- **名称一致**: {hierarchy.get('matched', 0)}",
                f"- **仅 {source_label}**: {len(hierarchy.get('only_a', []) or [])}",
                f"- **仅 {target_label}**: {len(hierarchy.get('only_b', []) or [])}",
            ]),
        })
    return sections


def resolve_compare_result_path(payload, params, input_source, input_target):
    """推导 compare_result 输出路径，优先使用沙盒 `.info`。"""
    output_path = (params.get('output_path') or '').strip()
    if output_path:
        return output_path

    info_dir = (
        params.get('info_dir')
        or payload.get('info_dir')
        or (payload.get('extra_params') or {}).get('info_dir')
    )
    if not info_dir:
        run_dir = payload.get('run_dir') or (payload.get('extra_params') or {}).get('run_dir')
        if not run_dir and payload.get('task_id'):
            run_dir = create_run_dir(
                payload.get('task_id'),
                payload.get('project', 'default'),
                payload.get('asset_name', 'untitled'),
                payload.get('submitted_at'),
            )
        if run_dir:
            info_dir = os.path.join(str(run_dir), '.info')

    if not info_dir:
        return ''

    stem_a = os.path.splitext(os.path.basename(input_source))[0]
    stem_b = os.path.splitext(os.path.basename(input_target))[0]
    return os.path.join(str(info_dir), f"{stem_a}_vs_{stem_b}_compare_result.json")


def write_compare_result(path, report, input_source, input_target, label_source, label_target):
    """写出标准 compare_result.v1。"""
    if is_protected_path(path):
        raise PermissionError(f'输出路径 "{path}" 位于只读/受保护区域，禁止写入。')

    out_dir = os.path.dirname(path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    payload = {
        "schema_version": "compare_result.v1",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "input_source": input_source,
            "input_target": input_target,
            "label_source": label_source,
            "label_target": label_target,
        },
        "compare": report,
    }
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return path


def load_info_from_path(path, lightweight_abc=True):
    """读取 .json 或 .abc 为 asset_info dict。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.abc':
        from core.abc_reader import read_abc_as_info
        return read_abc_as_info(path, lightweight=lightweight_abc)

    if ext == '.json':
        with open(path, 'r', encoding='utf-8') as fp:
            info = json.load(fp)
        ok, errors = validate(info)
        if not ok:
            raise ValueError(f'JSON 校验失败: {"; ".join(errors)}')
        return info

    if ext in ('.ma', '.mb', '.blend'):
        raise ValueError(
            f'不支持直接对比 DCC 源文件 ({ext})，请先转换为 _info.json 或 ABC。'
        )
    raise ValueError(f'不支持的文件类型: {ext}，支持 .json / .abc')


def generate_report(report, info_source_path, info_target_path, label_source, label_target,
                    source_file, target_file):
    """生成 Markdown 报告（几何配对为主，贴图仅在输入数据包含时展示）。"""
    paired = report["paired"]
    only_source = report["only_a"]
    only_target = report["only_b"]
    total = report["total_issues"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    status = "PASS" if total == 0 else "FAIL"

    n_identical = sum(1 for p in paired if p.get("actionability") == "IDENTICAL")
    n_orig_inject = sum(1 for p in paired if p.get("actionability") == "ORIG_INJECT")
    n_modified = sum(1 for p in paired if p.get("actionability") == "MODIFIED")
    n_merge = sum(1 for p in paired if p.get("actionability") == "MERGE")
    n_split = sum(1 for p in paired if p.get("actionability") == "SPLIT")

    n_out_identical = n_identical + n_orig_inject
    n_out_matched = n_modified + n_merge + n_split
    n_out_only_source = len(only_source)
    n_out_only_target = len(only_target)

    stem_source = os.path.splitext(os.path.basename(source_file or info_source_path))[0]
    asset_parts = stem_source.split("_")
    asset_name = asset_parts[2] if len(asset_parts) > 2 else stem_source

    lines = [
        f"# {asset_name} 资产对比 {status}",
        (
            f"{now} | source={label_source} target={label_target} | "
            f"identical {n_out_identical} | "
            f"matched_different {n_out_matched} | "
            f"only_source {n_out_only_source} | "
            f"only_target {n_out_only_target}"
        ),
        "",
        f"## source ({label_source}) 每个 mesh 相对 target ({label_target}) 的归属",
        "",
        "| 去向 | 数量 | 含义 |",
        "|------|------|------|",
        f"| **identical** | {n_out_identical} | source 在 target 中有几何等同对应 |",
        f"| **matched_different** | {n_out_matched} | source 在 target 中找到配对，但几何不完全一致 |",
        f"| **only_source** | {n_out_only_source} | 仅 source 存在，target 中无对应 |",
        f"| **only_target** | {n_out_only_target} | 仅 target 存在，source 中无对应 |",
        "",
        "## 对比来源",
        "",
    ]

    for label, src, data_path in [
        (label_source, source_file, info_source_path),
        (label_target, target_file, info_target_path),
    ]:
        lines.append(f"**{label}**")
        if src and src != data_path:
            lines.append(f"源文件: {src}")
        lines.append(f"数据: {data_path}")
        lines.append("")

    def _append_pairs(title, items, columns):
        if not items:
            return
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        lines.append(columns[0])
        lines.append(columns[1])
        for p in items[:20]:
            lines.append(
                f"| **{p.get('name_a', dag_short(p.get('dag_a', '')))}** "
                f"| **{p.get('name_b', dag_short(p.get('dag_b', '')))}** "
                f"| {p.get('vtx_a', 0)} vs {p.get('vtx_b', 0)} "
                f"| {p.get('match_pct_loose', '-')} "
                f"| {p.get('max_offset', 0)}cm |"
            )
        if len(items) > 20:
            lines.append(f"| ... | ... | ... | ... | 其他 {len(items) - 20} 个 |")
        lines.append("")

    _append_pairs(
        "ORIG_INJECT",
        [p for p in paired if p.get("actionability") == "ORIG_INJECT"],
        ("| source | target | 顶点 | 宽松匹配 | 最大偏差 |", "|---|---|---|---|---|"),
    )
    _append_pairs(
        "MODIFIED",
        [p for p in paired if p.get("actionability") == "MODIFIED"],
        ("| source | target | 顶点 | 宽松匹配 | 最大偏差 |", "|---|---|---|---|---|"),
    )
    _append_pairs(
        "MERGE/SPLIT",
        [p for p in paired if p.get("actionability") in ("MERGE", "SPLIT")],
        ("| source | target | 顶点 | 宽松匹配 | 最大偏差 |", "|---|---|---|---|---|"),
    )

    for label, entries in [
        (f"仅 {label_source}", only_source),
        (f"仅 {label_target}", only_target),
    ]:
        if not entries:
            continue
        lines.append(f"## {label} ({len(entries)})")
        lines.append("")
        for entry in entries[:20]:
            dag = entry.get("dag", "")
            name = entry.get("name", dag_short(dag))
            lines.append(f"- {name} ({entry.get('vtx', 0)} vtx)")
        if len(entries) > 20:
            lines.append(f"- ... 其他 {len(entries) - 20} 个")
        lines.append("")

    h = report.get("hierarchy", {})
    if h:
        lines.append("## 层级匹配")
        lines.append("")
        lines.append(
            f"名称一致 {h.get('matched', 0)}, "
            f"仅{label_source} {len(h.get('only_a', []))}, "
            f"仅{label_target} {len(h.get('only_b', []))}"
        )
        lines.append("")

    return "\n".join(lines)
