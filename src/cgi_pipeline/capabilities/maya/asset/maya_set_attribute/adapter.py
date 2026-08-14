"""Explicit placeholder for a documented operation without a local implementation."""

from cgi_pipeline.contracts import make_api_receipt
import time

def execute(params, context):
    return make_api_receipt(
        str(context.extras.get("_api_id") or ""),
        str(context.extras.get("_api_version") or "1.0.0"),
        "ERROR", time.perf_counter(), input_data=dict(params),
        error_code="API_IMPLEMENTATION_MISSING",
        error="该 API 只有目录定义，当前没有本地执行实现。",
        recovery_hint="使用已登记的 API 或先补充实现。",
    )
