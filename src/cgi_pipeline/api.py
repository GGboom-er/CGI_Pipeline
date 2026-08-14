"""Host-facing API discovery and local execution."""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

from .catalog import capability_help, get_capability, list_capabilities
from .contracts import make_api_receipt
from .execution import execute_api


def list(*, executor: str | None = None, capability: str | None = None,
         operation: str | None = None, stages: Iterable[str] | None = None,
         targets: Iterable[str] | None = None, access: str | None = None,
         surfaces: Iterable[str] | None = None) -> list[dict[str, Any]]:
    return list_capabilities(executor=executor, capability=capability, operation=operation,
                             stages=stages, targets=targets, access=access, surfaces=surfaces)


def help(api_id: str) -> dict[str, Any]:
    return capability_help(api_id)


def execute_local(api_id: str, params: Mapping[str, Any] | None = None, *, host: str,
                  host_modules: Mapping[str, Any] | None = None,
                  context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
        spec = get_capability(api_id)
    except KeyError as exc:
        return make_api_receipt(api_id, "", "ERROR", started_at,
                                error_code="API_NOT_FOUND", error=str(exc))
    if host not in spec["surfaces"] or "local" not in spec["execution_modes"]:
        return make_api_receipt(
            api_id, spec["version"], "ERROR", started_at,
            error_code="HOST_SURFACE_UNSUPPORTED",
            error=f"{api_id} cannot execute locally in host={host}",
            recovery_hint="Use api.help to inspect surfaces or call client.execute for service execution.",
        )
    raw_context = dict(context or {})
    raw_context.update({"host": host, "execution_mode": "local"})
    modules = dict(host_modules or {})
    if "cmds" in modules:
        raw_context["cmds_module"] = modules["cmds"]
    raw_context["host_modules"] = modules
    return execute_api(api_id, params, raw_context)
