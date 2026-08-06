"""Adapter for operations migrated into the unified API implementation shape."""

from __future__ import annotations

from typing import Any, Callable

from api.contract import ApiContext, ApiContractError, make_api_receipt


_STATUS_MAP = {
    "SUCCESS": "SUCCESS",
    "CHAIN_SUCCESS": "SUCCESS",
    "PARTIAL": "PARTIAL",
    "BLOCKED": "BLOCKED",
    "NEEDS_ATTENTION": "NEEDS_ATTENTION",
    "TIMEOUT": "TIMEOUT",
    "CANCELLED": "CANCELLED",
}


def execute_legacy(
    implementation: Callable[[dict[str, Any]], dict[str, Any]],
    params: dict[str, Any],
    context: ApiContext,
) -> dict[str, Any]:
    """Run one migrated implementation and normalize its receipt.

    The operation code keeps its small ``execute(payload)`` boundary; this
    adapter is the only place that translates it to the API contract.
    """
    import time

    started_at = time.perf_counter()
    api_id = str(context.extras.get("_api_id") or "")
    api_version = str(context.extras.get("_api_version") or "1.0.0")
    payload = {
        "task_id": str(context.extras.get("task_id") or ""),
        "api_id": api_id,
        "project": context.project,
        "asset_name": context.asset_name,
        "source_path": context.source_path,
        "parameters": dict(params),
        "api_params": dict(params),
        "run_dir": context.run_dir,
    }
    try:
        legacy = implementation(payload)
        if not isinstance(legacy, dict):
            raise ApiContractError("operation must return a receipt mapping")
        legacy_status = str(legacy.get("status") or "ERROR")
        status = _STATUS_MAP.get(legacy_status, "ERROR")
        output = dict(legacy.get("outputs") or {})
        if legacy.get("summary") is not None:
            output.setdefault("summary", legacy["summary"])
        if legacy.get("items") is not None:
            output.setdefault("items", legacy["items"])
        receipt = make_api_receipt(
            api_id,
            api_version,
            status,
            started_at,
            input_data={
                "params": dict(params),
                "project": context.project,
                "asset_name": context.asset_name,
                "source_path": context.source_path,
            },
            output={"legacy_status": legacy_status, **output},
            error_code=("API_OPERATION_ERROR" if status == "ERROR" else ""),
            error=str(legacy.get("error") or "") if status != "SUCCESS" else "",
            recovery_hint=str(legacy.get("recovery_hint") or ""),
            artifacts=[
                str(value)
                for value in output.values()
                if isinstance(value, str)
                and value.lower().endswith((".json", ".md", ".ma", ".mb", ".abc", ".blend", ".png"))
            ],
        )
        # The API contract uses ``output``.  ``outputs`` is an intentional
        # compatibility alias for the workflow/report renderer while chains
        # are being read from the same receipt.
        receipt["outputs"] = receipt["output"]
        receipt["summary"] = legacy.get("summary", {})
        return receipt
    except ApiContractError:
        raise
    except Exception as exc:
        return make_api_receipt(
            api_id,
            api_version,
            "ERROR",
            started_at,
            input_data={"params": dict(params)},
            error_code="API_OPERATION_ERROR",
            error=str(exc),
            recovery_hint="查看 API 任务报告中的完整 traceback。",
        )
