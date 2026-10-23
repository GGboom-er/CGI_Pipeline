"""Execute one registered capability inside an explicitly selected runtime."""

from __future__ import annotations

import time
import traceback
from typing import Any, Mapping

from .catalog import load_handler, snapshot as get_catalog_snapshot
from .contracts import (ApiContext, ApiContractError, make_api_receipt, resolve_mode,
                        validate_params, validate_receipt)


def execute_api(api_id: str, params: Mapping[str, Any] | None = None,
                context: Mapping[str, Any] | None = None, *,
                catalog_snapshot=None) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
        specs, version = get_catalog_snapshot() if catalog_snapshot is None else catalog_snapshot
        raw_context = dict(context or {})
        expected = raw_context.get("expected_catalog_version")
        if expected is not None and expected != version:
            return make_api_receipt(
                api_id, "", "ERROR", started_at,
                output={"catalog_version": version},
                error_code="CATALOG_VERSION_STALE",
                error=f"cached catalog version {expected} does not match host version {version}",
                recovery_hint="Refresh api_help and submit again; no handler was executed.",
            )
        spec = specs[api_id]
    except KeyError as exc:
        return make_api_receipt(api_id, "", "ERROR", started_at,
                                error_code="API_NOT_FOUND", error=str(exc),
                                recovery_hint="Use api.list or api.help to select a registered API.")
    except ApiContractError as exc:
        return make_api_receipt(api_id, "", "ERROR", started_at,
                                error_code="API_CONTRACT_ERROR", error=str(exc),
                                recovery_hint="Repair or regenerate the capability catalog before retrying.")
    try:
        execution_mode = str(raw_context.get("execution_mode") or "background")
        if execution_mode not in spec["execution_modes"]:
            raise ApiContractError(f"{api_id} does not support execution_mode={execution_mode}")
        host = str(raw_context.get("host") or "")
        if execution_mode == "local" and host not in spec["surfaces"]:
            raise ApiContractError(f"{api_id} does not support host surface={host}")
        if execution_mode == "foreground" and not raw_context.get("foreground_port"):
            raise ApiContractError("foreground API execution requires foreground_port")
        values = validate_params(spec, params)
        raw_context["_api_mode"] = resolve_mode(spec, values)
        raw_context.setdefault("_api_id", api_id)
        raw_context.setdefault("_api_version", spec["version"])
        ctx = ApiContext.from_mapping(raw_context, spec["executor"])
        result = load_handler(api_id, spec=spec)(dict(values), ctx)
        return validate_receipt(result, api_id=api_id, api_version=spec["version"])
    except ApiContractError as exc:
        return make_api_receipt(api_id, spec["version"], "ERROR", started_at,
                                error_code="API_CONTRACT_ERROR", error=str(exc),
                                recovery_hint="Read api.help for inputs, modes, and runtime surfaces.")
    except Exception as exc:
        return make_api_receipt(api_id, spec["version"], "ERROR", started_at,
                                error_code="API_EXECUTION_ERROR", error=str(exc),
                                recovery_hint="Inspect the traceback and verify the selected host context.",
                                artifacts=[traceback.format_exc()])


def execute_batch(calls: list[Mapping[str, Any]], *,
                  context: Mapping[str, Any] | None = None,
                  atomic: bool = True, catalog_snapshot=None) -> dict[str, Any]:
    """Execute several literal API calls in one DCC boundary.

    ``atomic`` is an abort policy: the first failed receipt stops later calls.
    Maya adapters may additionally provide ``cmds_module`` and undo the whole
    chunk; callers must inspect ``rolled_back`` rather than assuming rollback.
    """
    started_at = time.perf_counter()
    values = list(calls or [])
    if not values:
        return {'status': 'ERROR', 'error_code': 'BATCH_EMPTY',
                'error': 'batch 至少需要一个 API 调用', 'results': []}
    ctx = dict(context or {})
    selected_snapshot = get_catalog_snapshot() if catalog_snapshot is None else catalog_snapshot
    expected = ctx.get('expected_catalog_version')
    stale = expected is not None and expected != selected_snapshot[1]
    cmds = ctx.get('cmds_module')
    undo_open = False
    rolled_back = False
    if atomic and cmds is not None and not stale:
        try:
            cmds.undoInfo(openChunk=True, chunkName='cgi_execute_batch')
            undo_open = True
        except Exception:
            undo_open = False
    results: list[dict[str, Any]] = []
    try:
        for call in values:
            if not isinstance(call, Mapping) or not call.get('api_id'):
                result = {'status': 'ERROR', 'error_code': 'BATCH_CALL_INVALID',
                          'error': '每个 batch 项必须包含 api_id'}
            else:
                call_context = dict(ctx)
                if call.get('task_id'):
                    call_context['task_id'] = str(call['task_id'])
                result = execute_api(str(call['api_id']), call.get('params') or
                                     call.get('api_params') or {}, call_context,
                                     catalog_snapshot=selected_snapshot)
            results.append(result)
            if atomic and result.get('status') != 'SUCCESS':
                break
    finally:
        if undo_open:
            try:
                cmds.undoInfo(closeChunk=True)
                if any(r.get('status') != 'SUCCESS' for r in results):
                    cmds.undo()
                    rolled_back = True
            except Exception:
                rolled_back = False
    status = 'SUCCESS' if all(r.get('status') == 'SUCCESS' for r in results) and len(results) == len(values) else 'ERROR'
    return {
        'status': status, 'results': results,
        'completed': len(results), 'requested': len(values),
        'atomic': bool(atomic), 'rolled_back': rolled_back,
        'elapsed_sec': round(time.perf_counter() - started_at, 6),
    }
