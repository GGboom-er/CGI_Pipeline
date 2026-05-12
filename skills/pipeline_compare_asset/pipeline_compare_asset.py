# skills/compare_asset.py
# ── 资产对比统一入口 ──
#
# 纯 JSON/ABC 数据对比，不依赖任何 DCC。
# DCC 源文件必须由上游 skill 先转换为 _info.json 或 ABC。
# 对比引擎在 core/asset_info_schema.py。
# 报告通过 receipt['report_content'] 返回给主控，不自行落盘。

import os
import json
import time
import datetime

from core.receipt import make_receipt
from core.asset_info_schema import validate, compare
from core.path_guard import is_protected_path
from core.run_archive import create_run_dir



# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

def _auto_label(path):
    """
    从文件路径自动推断标签。
    支持: rig, tex, uv, model, anim, fx 等阶段。
    优先从目录结构推断（如 .../tex/texMaster/...），其次从文件名。
    """
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


def _dag_short(dag):
    """取 DAG 路径最后一段"""
    parts = dag.strip("|").split("|")
    return parts[-1].split(":")[-1] if parts else dag


def _generate_report(report, info_a_path, info_b_path, label_a, label_b,
                     source_a, source_b):
    """生成 Markdown 报告（几何配对为主，贴图仅在输入数据包含时展示）。"""
    paired = report["paired"]
    only_a = report["only_a"]
    only_b = report["only_b"]
    total = report["total_issues"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    status = "✅ PASS" if total == 0 else "❌ FAIL"

    # 算法层 7 标签统计（展开明细时用）
    n_identical   = sum(1 for p in paired if p.get("actionability") == "IDENTICAL")
    n_orig_inject = sum(1 for p in paired if p.get("actionability") == "ORIG_INJECT")
    n_modified    = sum(1 for p in paired if p.get("actionability") == "MODIFIED")
    n_merge       = sum(1 for p in paired if p.get("actionability") == "MERGE")
    n_split       = sum(1 for p in paired if p.get("actionability") == "SPLIT")

    # 用户视角 4 去向（source 侧每个 mesh 的归属）
    n_out_identical   = n_identical + n_orig_inject
    n_out_matched     = n_modified + n_merge + n_split
    n_out_only_source = len(only_a)
    n_out_only_target = len(only_b)

    lines = []

    # ── 标题 ──
    stem_a = os.path.splitext(os.path.basename(source_a or info_a_path))[0]
    asset_parts = stem_a.split("_")
    asset_name = asset_parts[2] if len(asset_parts) > 2 else stem_a

    lines.append(f"# {asset_name} 资产对比 {status}")
    lines.append(
        f"{now} | source={label_a} target={label_b} | "
        f"identical {n_out_identical} | "
        f"matched_different {n_out_matched} | "
        f"only_source {n_out_only_source} | "
        f"only_target {n_out_only_target}"
    )
    lines.append("")

    # ── 四去向概览 ──
    lines.append(f"## source ({label_a}) 每个 mesh 相对 target ({label_b}) 的归属")
    lines.append("")
    lines.append("| 去向 | 数量 | 含义 |")
    lines.append("|------|------|------|")
    lines.append(f"| **identical**         | {n_out_identical}   | source 在 target 中有几何等同对应（可直接复用） |")
    lines.append(f"| **matched_different** | {n_out_matched}     | source 在 target 中找到配对，但几何不完全一致 |")
    lines.append(f"| **only_source**       | {n_out_only_source} | 仅 source 存在，target 中无对应 |")
    lines.append(f"| **only_target**       | {n_out_only_target} | 仅 target 存在，source 中无对应 |")
    lines.append("")
    lines.append("> 算法层细分标签（IDENTICAL/ORIG_INJECT/MODIFIED/MERGE/SPLIT）见下方各分区。")
    lines.append("")

    # ── 对比来源 ──
    lines.append("## 对比来源")
    lines.append("")
    for label, src, json_path in [
        (label_a, source_a, info_a_path),
        (label_b, source_b, info_b_path),
    ]:
        input_type = f"{label}_json"
        if src and src != json_path:
            input_type = f"{label}_json (来源: {label} 源文件)"

        lines.append(f"**{input_type}**")
        if src and src != json_path:
            src_name = os.path.basename(src)
            src_dir = os.path.dirname(src).replace("\\", "/")
            lines.append(f"源文件: [{src_name}](file:///{src_dir}/)")
        json_name = os.path.basename(json_path)
        json_dir = os.path.dirname(json_path).replace("\\", "/")
        lines.append(f"JSON: [{json_name}](file:///{json_dir}/)")
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── ORIG_INJECT 项（几何一致，仅需注入新点/UV）──
    orig_inject_items = [p for p in paired if p.get("actionability") == "ORIG_INJECT"]
    if orig_inject_items:
        lines.append(f"## 🔁 ORIG_INJECT — 偷梁换柱 ({len(orig_inject_items)})")
        lines.append("")
        lines.append(f"| {label_a} | {label_b} | 顶点 | 宽松匹配 | 最大偏差 | step |")
        lines.append("|---|---|---|---|---|---|")
        for p in orig_inject_items:
            lines.append(
                f"| **{p['name_a']}** | **{p['name_b']}** "
                f"| {p['vtx_a']} vs {p['vtx_b']} "
                f"| {p.get('match_pct_loose', '-')} "
                f"| {p.get('max_offset', 0)}cm "
                f"| S{p.get('step', '?')} |"
            )
        lines.append("")

    # ── MODIFIED 项（有源 rig，需定向投射）──
    modified_items = [p for p in paired if p.get("actionability") == "MODIFIED"]
    if modified_items:
        lines.append(f"## ⚠️ MODIFIED — 改过的物体 ({len(modified_items)})")
        lines.append("")
        lines.append(f"| {label_a} | {label_b} | 顶点 | 宽松匹配 | 最大偏差 | 说明 |")
        lines.append("|---|---|---|---|---|---|")
        for p in modified_items:
            reason = ""
            if p["vtx_a"] != p["vtx_b"]:
                reason = f"点数不同({p['vtx_a']} vs {p['vtx_b']})"
            else:
                reason = p.get("precision_used", "部分拓扑变动")
            lines.append(
                f"| **{p['name_a']}** | **{p['name_b']}** "
                f"| {p['vtx_a']} vs {p['vtx_b']} "
                f"| {p.get('match_pct_loose', '-')} "
                f"| {p.get('max_offset', 0)}cm "
                f"| {reason} |"
            )
        lines.append("")

    # ── MERGE / SPLIT 项 ──
    merge_items = [p for p in paired if p.get("actionability") == "MERGE"]
    split_items = [p for p in paired if p.get("actionability") == "SPLIT"]
    if merge_items or split_items:
        lines.append(f"## 🔀 MERGE/SPLIT ({len(merge_items)} merge, {len(split_items)} split)")
        lines.append("")
        lines.append(f"| 类型 | {label_a} | {label_b} | 顶点 | 宽松匹配 |")
        lines.append("|---|---|---|---|---|")
        for p in merge_items + split_items:
            lines.append(
                f"| {p['actionability']} | **{p['name_a']}** | **{p['name_b']}** "
                f"| {p['vtx_a']} vs {p['vtx_b']} "
                f"| {p.get('match_pct_loose', '-')} |"
            )
        lines.append("")

    # ── 仅存项 ──
    for label, items in [
        (f"仅{label_a}（新增）", only_a),
        (f"仅{label_b}（已删）", only_b),
    ]:
        if not items:
            continue
        lines.append(f"## {label} ({len(items)})")
        lines.append("")
        lines.append("<details><summary>完整列表</summary>")
        lines.append("")
        for entry in items[:20]:
            dag = entry.get("dag", "")
            name = entry.get("name", _dag_short(dag))
            vtx = entry.get("vtx", 0)
            lines.append(f"**{name}** ({vtx} vtx)")
        if len(items) > 20:
            lines.append(f"- *...及其他 {len(items) - 20} 个*")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # ── 一致项统计 ──
    if n_identical > 0:
        lines.append(f"## ✅ 完全一致 ({n_identical})")
        lines.append("")

    # ── 层级匹配 ──
    h = report.get("hierarchy", {})
    if h:
        matched = h.get("matched", 0)
        h_only_a = h.get("only_a", [])
        h_only_b = h.get("only_b", [])
        lines.append(f"## 层级匹配")
        lines.append("")
        lines.append(f"名称一致 {matched}, 仅{label_a} {len(h_only_a)}, 仅{label_b} {len(h_only_b)}")
        if h_only_a:
            lines.append("")
            lines.append(f"**仅 {label_a}**: {', '.join(h_only_a)}")
        if h_only_b:
            lines.append("")
            lines.append(f"**仅 {label_b}**: {', '.join(h_only_b)}")
        lines.append("")



    # ── 贴图 ──
    tex = report.get("textures", {})
    if tex and (tex.get("a_count", 0) > 0 or tex.get("b_count", 0) > 0):
        lines.append(f"## 贴图")
        lines.append("")
        lines.append(
            f"{label_a} {tex.get('a_count', 0)}张, "
            f"{label_b} {tex.get('b_count', 0)}张, "
            f"仅{label_a} {tex.get('only_a', 0)}张, "
            f"仅{label_b} {tex.get('only_b', 0)}张"
        )
        for lst_key, lst_label in [("only_a_list", label_a), ("only_b_list", label_b)]:
            file_list = tex.get(lst_key, [])
            if file_list:
                lines.append("")
                lines.append(f"**仅 {lst_label}**: {', '.join(file_list)}")
        lines.append("")

    return "\n".join(lines)


def _resolve_output_path(payload, params, input_a, input_b):
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

    stem_a = os.path.splitext(os.path.basename(input_a))[0]
    stem_b = os.path.splitext(os.path.basename(input_b))[0]
    return os.path.join(str(info_dir), f"{stem_a}_vs_{stem_b}_compare_result.json")


def _write_compare_result(path, report, input_a, input_b, label_a, label_b):
    if is_protected_path(path):
        raise PermissionError(f'输出路径 "{path}" 位于只读/受保护区域，禁止写入。')

    out_dir = os.path.dirname(path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    payload = {
        "schema_version": "compare_result.v1",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "input_source": input_a,
            "input_target": input_b,
            "label_source": label_a,
            "label_target": label_b,
        },
        "compare": report,
    }
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return path





# ═══════════════════════════════════════════
# 技能入口
# ═══════════════════════════════════════════

def _load_input(path, label, t0):
    """
    读取输入文件并返回 (info_dict, error_receipt)。
    支持 .json 和 .abc 输入。
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == '.abc':
        # ABC → 直接用 PyAlembic 读取 mesh 信息（无需 Maya）
        try:
            from core.abc_reader import read_abc_as_info
            info = read_abc_as_info(path, lightweight=True)
            return info, None
        except FileNotFoundError as e:
            return None, make_receipt('pipeline_compare_asset', 'ERROR', t0, error=str(e))
        except RuntimeError as e:
            return None, make_receipt('pipeline_compare_asset', 'ERROR', t0,
                                      error=f'{label} ABC 读取失败: {e}')
        except Exception as e:
            return None, make_receipt('pipeline_compare_asset', 'ERROR', t0,
                                      error=f'{label} ABC 读取异常: {e}')

    elif ext == '.json':
        try:
            with open(path, 'r', encoding='utf-8') as fp:
                info = json.load(fp)
            ok, errors = validate(info)
            if not ok:
                return None, make_receipt('pipeline_compare_asset', 'ERROR', t0,
                                          error=f'{label} JSON 校验失败: {"; ".join(errors)}')
            return info, None
        except Exception as e:
            return None, make_receipt('pipeline_compare_asset', 'ERROR', t0,
                                      error=f'读取 {label} 失败: {e}')

    elif ext in ('.ma', '.mb', '.blend'):
        return None, make_receipt('pipeline_compare_asset', 'ERROR', t0,
                                  error=f'{label} 是 DCC 源文件 ({ext})，请先用 '
                                        f'maya_build_asset_info / blender_build_asset_info '
                                        f'采集 JSON，或用 blender_export_abc 导出 ABC。')
    else:
        return None, make_receipt('pipeline_compare_asset', 'ERROR', t0,
                                  error=f'{label} 不支持的文件类型: {ext}，'
                                        f'支持 .json / .abc')


def execute(payload: dict) -> dict:
    t0 = time.time()
    sub_steps = []
    params = payload.get('parameters', {})
    # 节点化命名（推荐）；老键名 input_a/input_b 保持向后兼容
    input_a = params.get('input_source') or params.get('input_a', '')
    input_b = params.get('input_target') or params.get('input_b', '')
    label_a = params.get('label_source') or params.get('label_a', '')
    label_b = params.get('label_target') or params.get('label_b', '')

    if not input_a or not input_b:
        return make_receipt('pipeline_compare_asset', 'ERROR', t0,
                            error='缺少必填参数: input_source 和 input_target')

    for path in (input_a, input_b):
        if not os.path.isfile(path):
            return make_receipt('pipeline_compare_asset', 'ERROR', t0,
                                error=f'文件不存在: {path}')

    output_path = _resolve_output_path(payload, params, input_a, input_b)
    if not output_path:
        return make_receipt(
            'pipeline_compare_asset',
            'ERROR',
            t0,
            error='无法确定输出路径: output_path 为空，且无法从任务沙盒推导 .info 目录',
        )

    # 自动推断标签
    if not label_a:
        label_a = _auto_label(input_a)
    if not label_b:
        label_b = _auto_label(input_b)

    # ── 读取输入（支持 JSON / ABC）──
    t_read = time.time()
    info_a, err = _load_input(input_a, label_a, t0)
    if err:
        return err

    info_b, err = _load_input(input_b, label_b, t0)
    if err:
        return err
    sub_steps.append({'name': '读取输入文件', 'elapsed_sec': round(time.time() - t_read, 3)})

    # ── 统一对比 ──
    t_cmp = time.time()
    report = compare(info_a, info_b, label_a=label_a, label_b=label_b)
    sub_steps.append({'name': 'KDTree 匹配与对比', 'elapsed_sec': round(time.time() - t_cmp, 3)})

    # ── 生成 MD 报告（不落盘，通过 receipt 传回主控）──
    t_rpt = time.time()
    source_a = info_a.get("source_file", input_a)
    source_b = info_b.get("source_file", input_b)

    md = _generate_report(
        report, input_a, input_b, label_a, label_b, source_a, source_b
    )

    sub_steps.append({'name': '生成报告', 'elapsed_sec': round(time.time() - t_rpt, 3)})

    # ── 落标准 compare_result，供独立对比审计/报告使用 ──
    t_out = time.time()
    try:
        output_path = _write_compare_result(
            output_path, report, input_a, input_b, label_a, label_b
        )
    except Exception as e:
        return make_receipt('pipeline_compare_asset', 'ERROR', t0,
                            error=f'写入 compare_result 失败: {e}')
    sub_steps.append({'name': '写入 compare_result', 'elapsed_sec': round(time.time() - t_out, 3)})

    total = report["total_issues"]
    n_paired = len(report["paired"])

    if total == 0:
        status_msg = f"对比通过 — {n_paired} mesh 完全一致"
    else:
        status_msg = f"配对 {n_paired}, 差异 {total}"

    receipt = make_receipt(
        skill_id='pipeline_compare_asset',
        status='SUCCESS',
        start_time=t0,
        summary_input=f'{os.path.basename(input_a)} vs {os.path.basename(input_b)}',
        summary_action=status_msg,
        summary_count=total,
        summary_label='差异',
        outputs={
            'output_path': output_path,
        },
        report_content=md,
    )
    return receipt

