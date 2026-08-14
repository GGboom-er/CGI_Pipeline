import time
import datetime

MAX_ITEMS = 20


def _now_str():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _ms_to_min(ms):
    return round(ms / 60000, 2)


def _sec_to_min(sec):
    return round(sec / 60, 2)


def make_receipt(
    api_id: str,
    status: str,
    start_time: float,
    input: dict = None,
    output: dict = None,
    summary_input: str = '',
    summary_action: str = '',
    summary_count: int = 0,
    summary_label: str = '',
    items: list = None,
    error: str = '',
    error_code: str = '',
    recovery_hint: str = '',
    report_content: str = '',
    artifacts: list = None,
    api_version: str = '',
) -> dict:
    if output is None:
        output = {}
    if input is None:
        input = {}
    if not isinstance(input, dict):
        raise TypeError('receipt.input 必须是 dict')
    if not isinstance(output, dict):
        raise TypeError('receipt.output 必须是 dict')

    elapsed_sec = round(time.time() - start_time, 3)
    elapsed_min = _sec_to_min(elapsed_sec)
    if not api_version:
        try:
            from cgi_pipeline.catalog import get_capability
            api_version = str(get_capability(api_id).get('version', '1.0.0'))
        except Exception:
            api_version = '1.0.0'
    receipt = {
        'api_version': api_version,
        'input': input,
        'output': output,
        'status': status,
        'elapsed_sec': elapsed_sec,
        'api_id': api_id,
        'elapsed_min': elapsed_min,
        'summary': {
            'input': summary_input,
            'action': summary_action,
            'output_count': summary_count,
            'output_label': summary_label,
        },
        'items': (items or [])[:MAX_ITEMS],
        'error_code': error_code,
        'error': error,
        'recovery_hint': recovery_hint,
        'artifacts': list(artifacts or []),
    }
    if report_content:
        receipt['report_content'] = report_content
    return receipt


def make_item(name: str, detail: str, elapsed_min: float = None) -> dict:
    item = {'name': name, 'detail': detail}
    if elapsed_min is not None:
        item['elapsed_min'] = elapsed_min
    return item


def _status_icon(status: str) -> str:
    return {
        'RUNNING': '…',
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
