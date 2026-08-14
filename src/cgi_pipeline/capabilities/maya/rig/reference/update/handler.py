"""Maya reference update API backed by the generic reference engine."""

from __future__ import annotations

import os
import time
from typing import Any

from cgi_pipeline.contracts import ApiContext, ApiContractError, make_api_receipt
from cgi_pipeline.capabilities.maya.rig.reference import engine as update_references


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path))).replace("\\", "/")


def _resolver_from_target_map(target_map: list[dict[str, Any]]):
    mapping: dict[str, tuple[str, str]] = {}
    for index, row in enumerate(target_map):
        if not isinstance(row, dict):
            raise ApiContractError(f"target_map[{index}] must be an object")
        old_path = row.get("old_path", row.get("oldPath", ""))
        new_path = row.get("new_path", row.get("newPath", ""))
        asset = row.get("asset", "")
        if not old_path or not new_path:
            raise ApiContractError(
                f"target_map[{index}] requires old_path and new_path"
            )
        mapping[_norm(old_path)] = (str(asset), str(new_path))

    def resolve(old_path: str) -> tuple[str, str]:
        return mapping.get(_norm(old_path), ("", ""))

    return resolve


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
    failures = [
        row
        for row in rows
        if row.get("status") == "ERROR"
        or row.get("status") == "SKIP_ERROR"
        or str(row.get("status", "")).startswith("VERIFY_")
    ]
    return {
        "reference_count": len(rows),
        "updated_count": counts.get("UPDATED", 0),
        "counts": counts,
        "failed_count": len(failures),
        "rows": rows,
    }


def execute(params: dict[str, Any], context: ApiContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    operation = params.get("operation")
    if operation not in {"preview", "apply"}:
        raise ApiContractError("operation must be one of: preview, apply")

    target_map = params.get("target_map")
    if not isinstance(target_map, list) or not target_map:
        raise ApiContractError(
            "target_map is required; project resolvers must provide explicit old/new paths"
        )

    resolver = _resolver_from_target_map(target_map)
    cmds_module = context.extras.get("cmds_module")
    skip_status = str(params.get("skip_status") or "SKIP_NOT_TARGET")
    if operation == "preview":
        rows = update_references.build_update_plan(
            resolver,
            cmds_module,
            skip_status=skip_status,
        )
    else:
        rows = update_references.apply_updates(
            resolver,
            cmds_module,
            skip_status=skip_status,
        )

    summary = _summarize(rows)
    failed_count = summary["failed_count"]
    if failed_count == 0:
        status = "SUCCESS"
        error_code = ""
        error = ""
    elif failed_count < len(rows):
        status = "PARTIAL"
        error_code = (
            "REFERENCE_VERIFY_FAILED" if operation == "apply" else "REFERENCE_PLAN_FAILED"
        )
        error = f"{failed_count} reference row(s) failed"
    else:
        status = "ERROR"
        error_code = (
            "REFERENCE_VERIFY_FAILED" if operation == "apply" else "REFERENCE_PLAN_FAILED"
        )
        error = f"{failed_count} reference row(s) failed"

    return make_api_receipt(
        str(context.extras.get("_api_id") or "maya.rig.reference.update"),
        str(context.extras.get("_api_version") or "1.0.0"),
        status,
        started_at,
        input_data={
            "operation": operation,
            "target_count": len(target_map),
            "source_path": context.source_path,
            "project": context.project,
            "asset_name": context.asset_name,
        },
        output={
            "operation": operation,
            "scene_path": context.source_path,
            **summary,
        },
        error_code=error_code,
        error=error,
        recovery_hint=(
            "Review output.rows and correct the project resolver target_map before retrying."
            if failed_count
            else ""
        ),
    )
