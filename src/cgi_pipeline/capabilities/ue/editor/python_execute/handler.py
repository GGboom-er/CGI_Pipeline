"""API receipt adapter for UE_MCP_Bridge Python execution."""

from __future__ import annotations

import time

from cgi_pipeline.contracts import ApiContext, make_api_receipt


def execute(params: dict, context: ApiContext) -> dict:
    started_at = time.perf_counter()
    from cgi_pipeline.hosts.ue.foreground_client import execute_python

    result = execute_python(
        params["port"],
        params["code"],
        params.get("description", ""),
        params["timeout_seconds"],
    )
    status = result["status"]
    return make_api_receipt(
        str(context.extras.get("_api_id") or "ue.editor.python.execute"),
        str(context.extras.get("_api_version") or "1.0.0"),
        status,
        started_at,
        input_data={
            "port": params["port"],
            "description": params.get("description", ""),
        },
        output={
            "result": result.get("result"),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "traceback": result.get("traceback", ""),
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
