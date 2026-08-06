"""Thin API runner; scheduling remains owned by the existing CGI runtime."""

from __future__ import annotations

import time
import traceback
from typing import Any, Mapping

from .contract import (
    ApiContext,
    ApiContractError,
    make_api_receipt,
    validate_params,
    validate_receipt,
)
from .registry import get_api, get_api_handler


def execute_api(
    api_id: str,
    params: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one deterministic API in an already selected DCC context."""
    started_at = time.perf_counter()
    try:
        spec = get_api(api_id)
    except KeyError as exc:
        return make_api_receipt(
            api_id,
            "",
            "ERROR",
            started_at,
            error_code="API_NOT_FOUND",
            error=str(exc),
            recovery_hint="Use list_apis or api_help to select a registered API.",
        )

    try:
        raw_context = dict(context or {})
        execution_mode = str(raw_context.get("execution_mode") or "background")
        if execution_mode not in spec["execution_modes"]:
            raise ApiContractError(
                f"{api_id} does not support execution_mode={execution_mode}"
            )
        if execution_mode == "foreground" and not raw_context.get("foreground_port"):
            raise ApiContractError("foreground API execution requires foreground_port")
        params = validate_params(spec, params)
        operation = params.get("operation")
        if operation is not None and operation not in spec["operation_modes"]:
            raise ApiContractError(
                f"{api_id} does not support operation={operation}; "
                f"choose one of {spec['operation_modes']}"
            )
        raw_context.setdefault("_api_id", api_id)
        raw_context.setdefault("_api_version", spec["version"])
        ctx = ApiContext.from_mapping(raw_context, spec["dcc"])
        handler = get_api_handler(api_id)
        result = handler(dict(params or {}), ctx)
        result = validate_receipt(
            result,
            api_id=api_id,
            api_version=spec["version"],
        )
        result.setdefault("elapsed_sec", round(time.perf_counter() - started_at, 3))
        return result
    except ApiContractError as exc:
        return make_api_receipt(
            api_id,
            spec["version"],
            "ERROR",
            started_at,
            error_code="API_CONTRACT_ERROR",
            error=str(exc),
            recovery_hint="Read api_help for inputs, modes, and preconditions.",
        )
    except Exception as exc:  # API boundary must return a stable receipt.
        return make_api_receipt(
            api_id,
            spec["version"],
            "ERROR",
            started_at,
            error_code="API_EXECUTION_ERROR",
            error=str(exc),
            recovery_hint="Inspect the traceback in the task report and verify the DCC context.",
            artifacts=[traceback.format_exc()],
        )
