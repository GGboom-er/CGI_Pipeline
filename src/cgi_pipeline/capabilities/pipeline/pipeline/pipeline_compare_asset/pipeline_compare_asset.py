# ── 资产对比统一入口 ──
#
# 纯 JSON/ABC 数据对比，不依赖任何 DCC。
# DCC 源文件必须由上游 API 先转换为 _info.json 或 ABC。
# 对比引擎在 cgi_pipeline.core.asset_info_schema。
# 报告通过 receipt['report_content'] 返回给主控，不自行落盘。

import os
import time
import json

from cgi_pipeline.core.receipt import make_receipt
from cgi_pipeline.core.asset_info_schema import compare
from cgi_pipeline.core.compare_result_io import (
    auto_label,
    build_compare_output_details,
    format_compare_summary,
    generate_report,
    load_info_from_path,
    resolve_compare_result_path,
    summarize_compare_outcomes,
    write_compare_result,
)



# ═══════════════════════════════════════════
# API入口
# ═══════════════════════════════════════════

def _load_input(path, label, t0):
    """
    读取输入文件并返回 (info_dict, error_receipt)。
    支持 .json 和 .abc 输入。
    """
    try:
        return load_info_from_path(path, lightweight_abc=True), None
    except Exception as e:
        return None, make_receipt(
            'pipeline_compare_asset', 'ERROR', t0,
            error=f'{label} 数据读取失败: {e}'
        )


def execute(payload: dict) -> dict:
    t0 = time.time()
    sub_steps = []
    params = payload.get('parameters', {})
    input_a = params.get('input_source', '')
    input_b = params.get('input_target', '')
    label_a = params.get('label_source', '')
    label_b = params.get('label_target', '')

    if not input_a or not input_b:
        return make_receipt('pipeline_compare_asset', 'ERROR', t0,
                            error='缺少必填参数: input_source 和 input_target')

    for path in (input_a, input_b):
        if not os.path.isfile(path):
            return make_receipt('pipeline_compare_asset', 'ERROR', t0,
                                error=f'文件不存在: {path}')

    output_path = resolve_compare_result_path(payload, params, input_a, input_b)
    if not output_path:
        return make_receipt(
            'pipeline_compare_asset',
            'ERROR',
            t0,
            error='无法确定输出路径: output_path 为空，且无法从任务沙盒推导 .info 目录',
        )

    # 自动推断标签
    if not label_a:
        label_a = auto_label(input_a)
    if not label_b:
        label_b = auto_label(input_b)

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

    md = generate_report(
        report, input_a, input_b, label_a, label_b, source_a, source_b
    )
    output_details = build_compare_output_details(
        report, input_a, input_b, label_a, label_b, source_a, source_b
    )

    sub_steps.append({'name': '生成报告', 'elapsed_sec': round(time.time() - t_rpt, 3)})

    # ── 落标准 compare_result，供独立对比审计/报告使用 ──
    t_out = time.time()
    try:
        output_path = write_compare_result(
            output_path, report, input_a, input_b, label_a, label_b
        )
        with open(output_path, 'r', encoding='utf-8') as fp:
            compare_result_payload = json.load(fp)
    except Exception as e:
        return make_receipt('pipeline_compare_asset', 'ERROR', t0,
                            error=f'写入 compare_result 失败: {e}')
    sub_steps.append({'name': '写入 compare_result', 'elapsed_sec': round(time.time() - t_out, 3)})

    counts = summarize_compare_outcomes(report)
    status_msg = format_compare_summary(report)

    receipt = make_receipt(
        api_id='pipeline_compare_asset',
        status='SUCCESS',
        start_time=t0,
        input={
            'input_source': input_a,
            'input_target': input_b,
            'output_path': output_path,
            'label_source': label_a,
            'label_target': label_b,
        },
        output={
            'output_path': output_path,
            'matched_total': counts['paired'],
            'matched_same': counts['matched_same'],
            'matched_different': counts['matched_different'],
            'only_source': counts['only_source'],
            'only_target': counts['only_target'],
            'blocking': counts['blocking'],
            'compare_result': compare_result_payload,
            **output_details,
        },
        summary_input=f'{os.path.basename(input_a)} vs {os.path.basename(input_b)}',
        summary_action=status_msg,
        summary_count=counts['blocking'],
        summary_label='问题',
        report_content=md,
    )
    return receipt
