"""Canonical receipt boundary for Maya deformation skin inheritance."""

from __future__ import annotations

import time

from cgi_pipeline.contracts import make_api_receipt

from .core import NeedsAttention, inherit_skin


def execute(params, context):
    started = time.perf_counter()
    api_id = str(context.extras.get("_api_id") or "maya.rig.maya_deformation_inherit_skin")
    version = str(context.extras.get("_api_version") or "1.0.0")
    try:
        output = inherit_skin(dict(params), context)
        return make_api_receipt(
            api_id, version, "SUCCESS", started, input_data=dict(params), output=output,
            artifacts=[value for value in output.values() if isinstance(value, str) and value],
        )
    except NeedsAttention as exc:
        return make_api_receipt(
            api_id, version, "NEEDS_ATTENTION", started, input_data=dict(params),
            output=exc.output, error_code="INHERIT_SKIN_NEEDS_ATTENTION", error=str(exc),
            recovery_hint="Inspect diagnostic_path and provide an explicit owner or influence mapping.",
            artifacts=[value for value in exc.output.values() if isinstance(value, str) and value],
        )
    except Exception as exc:
        return make_api_receipt(
            api_id, version, "ERROR", started, input_data=dict(params),
            error_code="MAYA_DEFORMATION_INHERIT_SKIN_FAILED", error=str(exc),
            recovery_hint="Verify the source rig, absolute groups, local influence names, and sandbox paths.",
        )
