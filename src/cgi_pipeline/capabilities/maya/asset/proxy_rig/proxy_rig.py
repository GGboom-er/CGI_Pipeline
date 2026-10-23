"""Receipt boundary for inspect, plan preview and deterministic Proxy construction."""
import time

from cgi_pipeline.core.receipt import make_receipt


def execute(payload):
    started = time.time()
    try:
        from .core import run
        status, output = run(payload)
        return make_receipt("proxy_rig", status, started, input=payload.get("parameters", {}),
                            output=output, summary_action="Inspect, validate or build the planned Proxy Rig",
                            summary_count=output.get("after", {}).get("meshes", output.get("mesh_count", 0)),
                            summary_label="output meshes")
    except ValueError as exc:
        return make_receipt("proxy_rig", "BLOCKED", started, error=str(exc),
                            error_code="PROXY_PLAN_PRECONDITION",
                            recovery_hint="Inspect the current scene and revise only affected plan decisions; do not retry a stale plan.")
    except ImportError as exc:
        return make_receipt("proxy_rig", "NEEDS_ATTENTION", started, error=str(exc),
                            error_code="PROXY_DEPENDENCY_REQUIRED",
                            recovery_hint="Provide the declared geometry/weight engine in the current Maya runtime.")
    except RuntimeError as exc:
        return make_receipt("proxy_rig", "ERROR", started, error=str(exc),
                            error_code="PROXY_BUILD_FAILED",
                            recovery_hint="Read the native-undo result and saved sandbox state before recovery; do not automatically replay.")
