"""Adapt operation functions to the canonical API handler contract."""

from __future__ import annotations

from typing import Any, Callable

from cgi_pipeline.contracts import ApiContext, ApiContractError, make_api_receipt


_STATUS_MAP = {
    "SUCCESS": "SUCCESS",
    "CHAIN_SUCCESS": "SUCCESS",
    "PARTIAL": "PARTIAL",
    "BLOCKED": "BLOCKED",
    "NEEDS_ATTENTION": "NEEDS_ATTENTION",
    "TIMEOUT": "TIMEOUT",
    "CANCELLED": "CANCELLED",
}


def execute_operation(
    implementation: Callable[[dict[str, Any]], dict[str, Any]],
    params: dict[str, Any],
    context: ApiContext,
) -> dict[str, Any]:
    """Run one operation implementation through the canonical API context.

    The operation code keeps its small ``execute(payload)`` boundary; this
    adapter supplies the framework context and canonical API identity.
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
        "extra_params": dict(context.extras.get("extra_params") or {}),
    }
    try:
        result = implementation(payload)
        if not isinstance(result, dict):
            raise ApiContractError("operation must return a receipt mapping")
        operation_status = str(result.get("status") or "ERROR")
        status = _STATUS_MAP.get(operation_status, "ERROR")
        output = dict(result.get("output") or {})
        if result.get("summary") is not None:
            output.setdefault("summary", result["summary"])
        if result.get("items") is not None:
            output.setdefault("items", result["items"])
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
            output=output,
            error_code=("API_OPERATION_ERROR" if status == "ERROR" else ""),
            error=str(result.get("error") or "") if status != "SUCCESS" else "",
            recovery_hint=str(result.get("recovery_hint") or ""),
            artifacts=[
                str(value)
                for value in output.values()
                if isinstance(value, str)
                and value.lower().endswith((".json", ".md", ".ma", ".mb", ".abc", ".blend", ".png"))
            ],
        )
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
