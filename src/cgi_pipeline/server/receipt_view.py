"""Client-facing projection of CGI task results.

Full receipts stay in the task audit; the public tools return a bounded
summary by default so recovery does not keep large API output in the caller's
context. ``detail=True`` (or ``get_task_result(task_id, detail=True)``) returns
the unprojected result.
"""

from __future__ import annotations

import json
import os

SUMMARY_LIMIT_BYTES = 4 * 1024
_TEXT_CAP = 500
_LIST_CAP = 20

# Identity, outcome and linkage first; later keys are dropped first when the budget is tight.
_ESSENTIAL = ('catalog_version', 'task_id', 'api_id', 'workflow_id', 'status', 'worker_status',
              'terminal', 'error_code', 'error_type', 'error', 'recovery_hint', 'message',
              'failed_index', 'failed_segment', 'submitted')
_LINKS = ('workspace_id', 'business_task_id', 'run_dir', 'report_path', 'workspace_report_path',
          'operation_dir', 'audit_path', 'manifest_path')
# Business outcome fields: kept verbatim when they fit, otherwise replaced by their shape.
_RESULTS = ('effective_parameters', 'save_requested', 'result_saved', 'saved_results', 'batch', 'outputs')
_OPTIONAL = ('step_checklist', 'scene_session', 'runtime', 'post_compare', 'timings_ms', 'step',
             'progress', 'event_type', 'last_status')
_RECEIPT_KEYS = ('api_id', 'status', 'error_code', 'error', 'recovery_hint', 'elapsed_sec')


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode('utf-8'))


def max_wait_sec() -> int:
    """Longest server-side wait that still returns before the MCP client gives up.

    Codex uses tool_timeout_sec=120; keep 10 s for transport and audit.
    """
    try:
        return max(1, int(os.getenv('CGI_MCP_MAX_WAIT_SEC', '110')))
    except ValueError:
        return 110


def _clip(value):
    if isinstance(value, str) and len(value) > _TEXT_CAP:
        return value[:_TEXT_CAP] + f'…[{len(value)} chars; detail=true]'
    return value


def _shape(value) -> dict:
    shape = {'omitted_bytes': _size(value), 'type': type(value).__name__}
    if isinstance(value, (list, dict)):
        shape['len'] = len(value)
    if isinstance(value, dict):
        shape['keys'] = list(value)[:_LIST_CAP]
    return shape


def _output(output, budget: int):
    """Keep small output fields verbatim and replace the rest by their shape."""
    if not isinstance(output, dict) or _size(output) <= budget:
        return output if _size(output) <= budget else _shape(output)
    kept = {}
    for key, value in output.items():
        candidate = value if _size(value) <= 256 else _shape(value)
        if _size(kept) + _size({key: candidate}) > budget:
            kept['…'] = f'{len(output) - len(kept)} more keys; detail=true'
            break
        kept[key] = candidate
    return kept


def _receipt(receipt: dict, budget: int) -> dict:
    row = {key: _clip(receipt[key]) for key in _RECEIPT_KEYS if receipt.get(key) not in (None, '')}
    artifacts = receipt.get('artifacts')
    if isinstance(artifacts, list) and artifacts:
        row['artifacts'] = artifacts[:_LIST_CAP]
        if len(artifacts) > _LIST_CAP:
            row['artifacts_total'] = len(artifacts)
    if 'output' in receipt:
        row['output'] = _output(receipt['output'], max(256, budget - _size(row)))
    return row


def _step_receipt(row: dict) -> dict:
    receipt = row.get('receipt') if isinstance(row.get('receipt'), dict) else {}
    compact = {key: row[key] for key in ('api_id', 'status', 'entry_index', 'result_index') if key in row}
    for key in ('error_code', 'error'):
        if receipt.get(key):
            compact[key] = _clip(receipt[key])
    if receipt.get('artifacts'):
        compact['artifacts'] = receipt['artifacts'][:3]
    return compact


def project_result(result: dict, detail: bool = False) -> dict:
    """Return ``result`` unchanged for detail, otherwise a summary within SUMMARY_LIMIT_BYTES."""
    if detail or not isinstance(result, dict):
        return result
    full_bytes = _size(result)
    if full_bytes <= SUMMARY_LIMIT_BYTES:
        return result
    view = {key: _clip(result[key]) for key in (*_ESSENTIAL, *_LINKS) if key in result}
    for key in _RESULTS:
        if key in result:
            value = result[key]
            view[key] = value if _size(value) <= SUMMARY_LIMIT_BYTES // 4 else _shape(value)
    view['receipt_ref'] = {
        'task_id': result.get('task_id'),
        'full_bytes': full_bytes,
        'read': 'get_task_result(task_id, detail=true)',
        **({'audit_path': result['audit_path']} if result.get('audit_path') else {}),
    }
    if isinstance(result.get('receipt'), dict):
        view['receipt'] = _receipt(result['receipt'], SUMMARY_LIMIT_BYTES - _size(view) - 64)
    elif isinstance(result.get('receipt_ref'), dict):
        view['receipt_ref']['inline_ref'] = result['receipt_ref']
    if isinstance(result.get('step_receipts'), list):
        rows = result['step_receipts'][:_LIST_CAP]
        # Small per-step receipts (typical literal batches) stay inline; large ones are summarized.
        share = max(256, (SUMMARY_LIMIT_BYTES - _size(view) - 512) // max(1, len(rows)))
        view['step_receipts'] = [row if _size(row) <= share else _step_receipt(row) for row in rows]
        if len(result['step_receipts']) > _LIST_CAP:
            view['step_receipts_total'] = len(result['step_receipts'])
    if isinstance(result.get('steps'), list):
        steps = result['steps']
        failed = [step for step in steps if step.get('status') not in ('SUCCESS', 'RUNNING')]
        view['steps_summary'] = {'total': len(steps), 'failed': failed[:_LIST_CAP]}
    for key in _OPTIONAL:
        if key in result and _size(view) + _size({key: result[key]}) <= SUMMARY_LIMIT_BYTES:
            view[key] = result[key]
    # Last resort: shrink large summary members rather than exceed the budget.
    for key in ('step_receipts', 'steps_summary', 'receipt'):
        if _size(view) <= SUMMARY_LIMIT_BYTES:
            break
        if key == 'receipt' and isinstance(view.get('receipt'), dict):
            view['receipt'] = {k: v for k, v in view['receipt'].items() if k != 'output'}
            view['receipt']['output'] = _shape(result['receipt'].get('output'))
        elif key in view:
            view[key] = _shape(view[key])
    return view
