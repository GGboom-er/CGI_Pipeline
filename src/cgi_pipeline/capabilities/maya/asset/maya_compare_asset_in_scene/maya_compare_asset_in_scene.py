# ── Maya 场景内资产对比 ──
#
# 在当前已打开的 target rig 场景内采集 ShapeOrig 信息，读取 source 侧
# ABC/_info.json 后调用同一套 compare 算法，并写出 compare_result.json。

import os
import time
import json

import maya.cmds as cmds

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
from cgi_pipeline.core.receipt import make_receipt
from cgi_pipeline.hosts.maya.asset_info_collector import collect_scene_info, normalize_cache_group_param


def _resolve_input_source(params):
    return (params.get('input_source') or '').strip()


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {}) or {}

    input_source = _resolve_input_source(params)
    cache_group = normalize_cache_group_param(params.get('cache_group'))
    label_source = (params.get('label_source') or '').strip()
    label_target = (params.get('label_target') or 'rig').strip()

    if not input_source:
        return make_receipt(
            'maya_compare_asset_in_scene', 'ERROR', t0,
            error='缺少必填参数 input_source。'
        )
    if not os.path.isfile(input_source):
        return make_receipt(
            'maya_compare_asset_in_scene', 'ERROR', t0,
            error=f'文件不存在: {input_source}'
        )
    if not cache_group:
        return make_receipt(
            'maya_compare_asset_in_scene', 'ERROR', t0,
            error='缺少必填参数 cache_group。请由 workflow 从项目配置传入。'
        )

    target_scene = cmds.file(query=True, sceneName=True) or 'current_maya_scene'
    output_path = resolve_compare_result_path(
        payload, params, input_source, target_scene
    )
    if not output_path:
        return make_receipt(
            'maya_compare_asset_in_scene', 'ERROR', t0,
            error='无法确定输出路径: output_path 为空，且无法从任务沙盒推导 .info 目录。'
        )

    if not label_source:
        label_source = auto_label(input_source)

    try:
        source_info = load_info_from_path(input_source, lightweight_abc=True)
    except Exception as e:
        return make_receipt(
            'maya_compare_asset_in_scene', 'ERROR', t0,
            error=f'读取 source 数据失败: {e}'
        )

    try:
        target_info = collect_scene_info(cache_group)
    except RuntimeError as e:
        return make_receipt(
            'maya_compare_asset_in_scene', 'ERROR', t0,
            error=str(e)
        )

    # RIG 来源组里无 orig 的 mesh = 未绑定，已被 collect 摘出对比。就地归入 NoneRig 显示层，
    # 让绑定师一眼看到哪些没绑；这些件不参与对比、不复用。
    nonrig_meshes = target_info.get('nonrig_meshes', []) or []
    nonrig_layer_count = 0
    if nonrig_meshes:
        try:
            if not cmds.objExists('NoneRig'):
                cmds.createDisplayLayer(name='NoneRig', empty=True)
            valid = [m for m in nonrig_meshes if cmds.objExists(m)]
            if valid:
                cmds.editDisplayLayerMembers('NoneRig', *valid, noRecurse=True)
                nonrig_layer_count = len(valid)
        except Exception as e:
            cmds.warning(f'NoneRig 层归类失败: {e}')

    try:
        report = compare(
            source_info, target_info,
            label_a=label_source,
            label_b=label_target,
        )
    except Exception as e:
        return make_receipt(
            'maya_compare_asset_in_scene', 'ERROR', t0,
            error=f'对比失败: {e}'
        )

    try:
        output_path = write_compare_result(
            output_path, report,
            input_source, target_scene,
            label_source, label_target,
        )
        with open(output_path, 'r', encoding='utf-8') as fp:
            compare_result_payload = json.load(fp)
    except Exception as e:
        return make_receipt(
            'maya_compare_asset_in_scene', 'ERROR', t0,
            error=f'写入 compare_result 失败: {e}'
        )

    md = generate_report(
        report, input_source, target_scene,
        label_source, label_target,
        source_info.get('source_file', input_source),
        target_info.get('source_file', target_scene),
    )
    output_details = build_compare_output_details(
        report, input_source, target_scene,
        label_source, label_target,
        source_info.get('source_file', input_source),
        target_info.get('source_file', target_scene),
    )

    counts = summarize_compare_outcomes(report)
    status_msg = format_compare_summary(report)

    return make_receipt(
        api_id='maya_compare_asset_in_scene',
        status='SUCCESS',
        start_time=t0,
        input={
            'source_path': target_scene,
            'input_source': input_source,
            'output_path': output_path,
            'cache_group': cache_group,
            'label_source': label_source,
            'label_target': label_target,
        },
        output={
            'output_path': output_path,
            'matched_total': counts['paired'],
            'matched_same': counts['matched_same'],
            'matched_different': counts['matched_different'],
            'only_source': counts['only_source'],
            'only_target': counts['only_target'],
            'blocking': counts['blocking'],
            'nonrig_count': nonrig_layer_count,
            'nonrig_meshes': [m.split('|')[-1] for m in nonrig_meshes],
            'compare_result': compare_result_payload,
            **output_details,
        },
        summary_input=f'{os.path.basename(input_source)} vs 当前 Maya 场景',
        summary_action=status_msg,
        summary_count=counts['blocking'],
        summary_label='问题',
        report_content=md,
    )
