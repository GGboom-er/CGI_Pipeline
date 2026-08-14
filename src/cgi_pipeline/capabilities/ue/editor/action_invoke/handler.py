"""API receipt adapter for native UE_MCP_Bridge actions."""

from __future__ import annotations

import time

from cgi_pipeline.contracts import ApiContext, make_api_receipt


def execute(params: dict, context: ApiContext) -> dict:
    started_at = time.perf_counter()
    from cgi_pipeline.hosts.ue.foreground_client import execute_action

    result = execute_action(
        params["port"], params["method"], params["arguments"], params["timeout_seconds"],
    )
    status = result["status"]
    return make_api_receipt(
        str(context.extras.get("_api_id") or "ue.editor.action.invoke"),
        str(context.extras.get("_api_version") or "1.0.0"),
        status,
        started_at,
        input_data={"port": params["port"], "method": params["method"]},
        output={
            "result": result.get("result"),
            "session": result.get("session", {}),
            "bridge_protocol": result.get("bridge_protocol", ""),
            "progress": result.get("progress", []),
        },
        error_code=str(result.get("error_type") or result.get("error_code") or ""),
        error=str(result.get("error") or ""),
        recovery_hint=(
            "Use pipeline.session.discover to verify the selected UE Editor bridge."
            if status != "SUCCESS" else ""
        ),
    )
