# skills/export_abc_auto.py
# ── 自动路由 ABC 导出（pipeline 类，根据文件扩展名选择 DCC）──

import os
import sys
import time
import json
import logging

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT
from core.receipt import make_receipt
from core.run_archive import create_run_dir

_EXT_TO_DCC = {
    '.blend': 'blender',
    '.ma': 'maya',
    '.mb': 'maya',
}

_DCC_TO_SKILL = {
    'blender': 'blender_export_abc',
    'maya': 'maya_export_abc',
}

logger = logging.getLogger(__name__)


def _detect_dcc(source_path: str) -> str | None:
    ext = os.path.splitext(source_path)[1].lower()
    return _EXT_TO_DCC.get(ext)


def _compute_abc_path(payload: dict, source_path: str) -> str | None:
    """根据任务沙盒 `.info` 计算 abc 输出路径。"""
    params = payload.get('parameters', {}) or {}
    abc_path = (params.get('abc_path') or '').strip()
    if abc_path:
        return abc_path

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
        return None

    source_stem = os.path.splitext(os.path.basename(source_path or 'asset'))[0] or 'asset'
    return os.path.join(str(info_dir), f'{source_stem}.abc')


def execute(payload: dict) -> dict:
    t0 = time.time()
    source_path = payload.get('source_path', '')

    if not source_path:
        return make_receipt('pipeline_export_abc_auto', 'ERROR', t0,
                            error='source_path 为空')

    if not os.path.isfile(source_path):
        return make_receipt('pipeline_export_abc_auto', 'ERROR', t0,
                            error=f'文件不存在: {source_path}')

    dcc_type = _detect_dcc(source_path)
    if not dcc_type:
        ext = os.path.splitext(source_path)[1]
        return make_receipt('pipeline_export_abc_auto', 'ERROR', t0,
                            error=f'不支持的文件类型: {ext}，支持 .blend/.ma/.mb')

    skill_id = _DCC_TO_SKILL[dcc_type]

    from core.dcc_factory import create_worker, open_source_file

    worker = None
    try:
        worker = create_worker(dcc_type)

        task_id = payload.get('task_id', 'export_abc_auto')
        ok, err = open_source_file(worker, task_id, source_path, dcc_type)
        if not ok:
            return make_receipt('pipeline_export_abc_auto', 'ERROR', t0,
                                error=f'{dcc_type} 打开文件失败: {err}')

        inner_params = dict(payload.get('parameters', {}))
        if 'abc_path' not in inner_params:
            auto_abc = _compute_abc_path(payload, source_path)
            if auto_abc:
                inner_params['abc_path'] = auto_abc
            else:
                return make_receipt(
                    'pipeline_export_abc_auto',
                    'ERROR',
                    t0,
                    error='无法确定输出路径: abc_path 为空，且无法从任务沙盒推导 .info 目录',
                )

        inner_payload = {
            'task_id': f'{task_id}_export',
            'skill_id': skill_id,
            'source_path': source_path,
            'project': payload.get('project', 'default'),
            'asset_name': payload.get('asset_name', 'untitled'),
            'run_dir': payload.get('run_dir'),
            'info_dir': payload.get('info_dir') or (payload.get('extra_params') or {}).get('info_dir'),
            'extra_params': payload.get('extra_params') or {},
            'parameters': inner_params,
        }

        result = worker.run_skill(inner_payload)

        detail = result.get('detail', '{}')
        try:
            inner_result = json.loads(detail) if isinstance(detail, str) else detail
        except (json.JSONDecodeError, TypeError):
            inner_result = {'raw': detail}

        inner_result['dcc_type'] = dcc_type
        inner_result['routed_skill'] = skill_id

        status = result.get('status', inner_result.get('status', 'SUCCESS'))
        inner_summary = inner_result.get('summary', {})
        inner_outputs = inner_result.get('outputs', {}) or {}
        routed_result = {
            'dcc_type': dcc_type,
            'routed_skill': skill_id,
            'inner_status': inner_result.get('status', status),
        }
        if isinstance(inner_outputs.get('result'), dict):
            routed_result['child_result'] = inner_outputs.get('result')
        return make_receipt(
            skill_id='pipeline_export_abc_auto',
            status=status,
            start_time=t0,
            summary_input=os.path.basename(source_path),
            summary_action=f'自动路由 → {dcc_type} ABC 导出',
            summary_count=inner_summary.get('output_count', 0),
            summary_label=inner_summary.get('output_label', ''),
            outputs={
                'output_path': inner_outputs.get('output_path'),
                'report_path': inner_outputs.get('report_path'),
                'result': routed_result,
            },
        )

    except Exception as e:
        import traceback
        return make_receipt('pipeline_export_abc_auto', 'ERROR', t0,
                            error=f'{type(e).__name__}: {e}',
                            recovery_hint=traceback.format_exc())
    finally:
        if worker:
            try:
                worker.shutdown()
            except Exception as e:
                logger.warning('关闭 DCC worker 失败: %s', e, exc_info=True)
