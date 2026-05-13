import time
import datetime
from pathlib import Path

from core.bootstrap import cfg as _cfg

MAX_ITEMS = 20

PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)
REPORTS_DIR = PROJECT_ROOT / 'reports'


def _now_str():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _ms_to_min(ms):
    return round(ms / 60000, 2)


def _sec_to_min(sec):
    return round(sec / 60, 2)


def make_receipt(
    skill_id: str,
    status: str,
    start_time: float,
    input: dict = None,
    output: dict = None,
    summary_input: str = '',
    summary_action: str = '',
    summary_count: int = 0,
    summary_label: str = '',
    items: list = None,
    outputs: dict = None,
    error: str = '',
    recovery_hint: str = '',
    report_content: str = '',
    report_sections: list = None,
) -> dict:
    if output is None:
        output = outputs or {}
    elif outputs:
        merged = dict(outputs)
        merged.update(output)
        output = merged
    if input is None:
        input = {}
    if not isinstance(input, dict):
        raise TypeError('receipt.input 必须是 dict')
    if not isinstance(output, dict):
        raise TypeError('receipt.output 必须是 dict')

    elapsed_sec = round(time.time() - start_time, 3)
    elapsed_min = _sec_to_min(elapsed_sec)
    receipt = {
        'skill': skill_id,
        'input': input,
        'output': output,
        'status': status,
        'elapsed_sec': elapsed_sec,
        'skill_id': skill_id,
        'elapsed_min': elapsed_min,
        'summary': {
            'input': summary_input,
            'action': summary_action,
            'output_count': summary_count,
            'output_label': summary_label,
        },
        'items': (items or [])[:MAX_ITEMS],
        'outputs': output,
    }
    if error:
        receipt['error'] = error
    if recovery_hint:
        receipt['recovery_hint'] = recovery_hint
    if report_content:
        receipt['report_content'] = report_content
    if report_sections:
        receipt['report_sections'] = report_sections
    return receipt


def make_item(name: str, detail: str, elapsed_min: float = None) -> dict:
    item = {'name': name, 'detail': detail}
    if elapsed_min is not None:
        item['elapsed_min'] = elapsed_min
    return item


def _status_icon(status: str) -> str:
    return {
        'SUCCESS': '✓', 'ERROR': '✗', 'PARTIAL': '⚠',
        'BLOCKED': '🛑', 'AUDIT_FAILED': '✗', 'NEEDS_ATTENTION': '⏸',
        'CHAIN_ABORTED': '✗', 'CHAIN_BLOCKED': '🛑', 'CHAIN_AUDIT_FAILED': '✗',
        'WORKFLOW_ABORTED': '✗', 'WORKFLOW_ERROR': '✗', 'WORKFLOW_AUDIT_FAILED': '✗',
        'SKIPPED': '⊘',
    }.get(status, '?')


def _format_elapsed(elapsed_min: float) -> str:
    """将分钟数格式化为人类可读的耗时字符串。"""
    if elapsed_min is None or elapsed_min < 0:
        return '-'
    secs = elapsed_min * 60
    if secs < 60:
        return f'{secs:.1f}s'
    return f'{elapsed_min:.2f} min'


def get_report_path(asset_name: str, task_id: str) -> str:
    """兜底路径：全局 reports/ 目录。正常路径应使用 write_task_report skill
    落沙盒（{run_dir}/REPORT.md）。保留此函数仅供诊断和 manifest 回填。
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_asset = asset_name or 'untitled'
    return str(REPORTS_DIR / f'{safe_asset}_{task_id}.md')
