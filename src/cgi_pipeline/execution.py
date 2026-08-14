"""Execute one registered capability inside an explicitly selected runtime."""

from __future__ import annotations

import time
import traceback
from typing import Any, Mapping

from .catalog import get_capability, load_handler
from .contracts import ApiContext, ApiContractError, make_api_receipt, validate_params, validate_receipt


def execute_api(api_id: str, params: Mapping[str, Any] | None = None,
                context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
        spec = get_capability(api_id)
    except KeyError as exc:
        return make_api_receipt(api_id, "", "ERROR", started_at,
                                error_code="API_NOT_FOUND", error=str(exc),
                                recovery_hint="Use api.list or api.help to select a registered API.")
    try:
        raw_context = dict(context or {})
        execution_mode = str(raw_context.get("execution_mode") or "background")
        if execution_mode not in spec["execution_modes"]:
            raise ApiContractError(f"{api_id} does not support execution_mode={execution_mode}")
        host = str(raw_context.get("host") or "")
        if execution_mode == "local" and host not in spec["surfaces"]:
            raise ApiContractError(f"{api_id} does not support host surface={host}")
        if execution_mode == "foreground" and not raw_context.get("foreground_port"):
            raise ApiContractError("foreground API execution requires foreground_port")
        values = validate_params(spec, params)
        mode = values.get("operation")
        if mode is not None and mode not in spec["modes"]:
            raise ApiContractError(f"{api_id} does not support operation={mode}; choose one of {spec['modes']}")
        raw_context.setdefault("_api_id", api_id)
        raw_context.setdefault("_api_version", spec["version"])
        ctx = ApiContext.from_mapping(raw_context, spec["executor"])
        result = load_handler(api_id)(dict(values), ctx)
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
