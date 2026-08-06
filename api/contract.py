"""Shared API metadata, execution context, and receipt contracts."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping


class ApiContractError(ValueError):
    """Raised when an API manifest or invocation violates the contract."""


API_STATUSES = {
    "SUCCESS",
    "PARTIAL",
    "ERROR",
    "BLOCKED",
    "NEEDS_ATTENTION",
    "TIMEOUT",
    "CANCELLED",
}

_API_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_ALLOWED_DCC = {"maya", "blender", "ue", "pipeline"}
_ALLOWED_DOMAINS = {"asset", "rig", "animation", "pipeline"}
_ALLOWED_TIERS = {"read", "write", "destructive"}
_ALLOWED_EXECUTION_MODES = {"foreground", "background"}
_ALLOWED_OPERATION_MODES = {"inspect", "preview", "apply"}
_REQUIRED_SPEC_FIELDS = {
    "api_id",
    "version",
    "dcc",
    "domain",
    "action",
    "tier",
    "execution_modes",
    "operation_modes",
    "inputs",
    "preconditions",
    "side_effects",
    "idempotent",
    "outputs",
    "statuses",
    "errors",
    "recovery",
    "help",
    "tests",
    "handler",
}

_REQUIRED_RECEIPT_FIELDS = {
    "api_id",
    "api_version",
    "status",
    "input",
    "output",
    "elapsed_sec",
    "error_code",
    "error",
    "recovery_hint",
    "artifacts",
}


def validate_spec(spec: Mapping[str, Any], source: str = "") -> dict[str, Any]:
    """Validate and return a normalized API manifest."""
    if not isinstance(spec, Mapping):
        raise ApiContractError(f"API manifest must be a mapping: {source}")

    missing = sorted(_REQUIRED_SPEC_FIELDS - set(spec))
    if missing:
        raise ApiContractError(
            f"API manifest missing fields {missing}: {source or spec.get('api_id', '')}"
        )

    result = dict(spec)
    api_id = result["api_id"]
    if not isinstance(api_id, str) or not _API_ID_RE.fullmatch(api_id):
        raise ApiContractError(f"invalid api_id: {api_id!r}")
    if not isinstance(result["version"], str) or not result["version"].strip():
        raise ApiContractError(f"invalid version for {api_id}")
    if result["dcc"] not in _ALLOWED_DCC:
        raise ApiContractError(f"invalid dcc for {api_id}: {result['dcc']!r}")
    if result["domain"] not in _ALLOWED_DOMAINS:
        raise ApiContractError(f"invalid domain for {api_id}: {result['domain']!r}")
    if result["tier"] not in _ALLOWED_TIERS:
        raise ApiContractError(f"invalid tier for {api_id}: {result['tier']!r}")
    if not isinstance(result["action"], str) or not result["action"].strip():
        raise ApiContractError(f"invalid action for {api_id}")
    if not isinstance(result["execution_modes"], list) or not result["execution_modes"]:
        raise ApiContractError(f"execution_modes must be a non-empty list: {api_id}")
    invalid_modes = set(result["execution_modes"]) - _ALLOWED_EXECUTION_MODES
    if invalid_modes:
        raise ApiContractError(f"invalid execution_modes for {api_id}: {sorted(invalid_modes)}")
    if not isinstance(result["operation_modes"], list) or not result["operation_modes"]:
        raise ApiContractError(f"operation_modes must be a non-empty list: {api_id}")
    invalid_operations = set(result["operation_modes"]) - _ALLOWED_OPERATION_MODES
    if invalid_operations:
        raise ApiContractError(
            f"invalid operation_modes for {api_id}: {sorted(invalid_operations)}"
        )
    if not isinstance(result["idempotent"], bool):
        raise ApiContractError(f"idempotent must be boolean: {api_id}")
    if not isinstance(result["handler"], str) or ":" not in result["handler"]:
        raise ApiContractError(f"handler must be module:function: {api_id}")

    for field_name in ("inputs", "outputs", "statuses", "errors", "help", "tests"):
        if not isinstance(result[field_name], (dict, list, str)):
            raise ApiContractError(f"invalid {field_name} for {api_id}")
    if isinstance(result["statuses"], list):
        invalid_statuses = set(result["statuses"]) - API_STATUSES
        if invalid_statuses:
            raise ApiContractError(
                f"invalid statuses for {api_id}: {sorted(invalid_statuses)}"
            )
    for field_name in ("preconditions", "side_effects", "recovery"):
        if not isinstance(result[field_name], (list, dict, str)):
            raise ApiContractError(f"invalid {field_name} for {api_id}")

    result["execution_modes"] = list(result["execution_modes"])
    result["operation_modes"] = list(result["operation_modes"])
    result["source"] = source
    return result


def validate_params(spec: Mapping[str, Any], params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the manifest-declared API input shape before DCC startup."""
    values = dict(params or {})
    inputs = spec.get("inputs", {})
    if not isinstance(inputs, Mapping):
        return values
    for name, definition in inputs.items():
        definition = definition if isinstance(definition, Mapping) else {}
        if definition.get("required") and name not in values:
            raise ApiContractError(f"missing required API input: {name}")
        if name not in values:
            if "default" in definition:
                values[name] = definition["default"]
            continue
        choices = definition.get("choices")
        if choices and values[name] not in choices:
            raise ApiContractError(f"invalid {name}={values[name]!r}; choose one of {choices}")
        expected = definition.get("type")
        if expected in {"string", "str"} and not isinstance(values[name], str):
            raise ApiContractError(f"{name} must be a string")
        if expected in {"array", "list"} and not isinstance(values[name], list):
            raise ApiContractError(f"{name} must be an array")
        if expected in {"object", "dict"} and not isinstance(values[name], dict):
            raise ApiContractError(f"{name} must be an object")
    return values


@dataclass(frozen=True)
class ApiContext:
    """Runtime context shared by API handlers without embedding DCC logic."""

    dcc: str
    execution_mode: str = "background"
    source_path: str = ""
    project: str = ""
    asset_name: str = ""
    run_dir: str = ""
    foreground_port: int | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, context: Mapping[str, Any] | None, dcc: str) -> "ApiContext":
        raw = dict(context or {})
        port = raw.get("foreground_port")
        if port not in (None, ""):
            try:
                port = int(port)
            except (TypeError, ValueError) as exc:
                raise ApiContractError("foreground_port must be an integer") from exc
        known = {
            "dcc",
            "execution_mode",
            "source_path",
            "project",
            "asset_name",
            "run_dir",
            "foreground_port",
        }
        return cls(
            dcc=dcc,
            execution_mode=str(raw.get("execution_mode") or "background"),
            source_path=str(raw.get("source_path") or ""),
            project=str(raw.get("project") or ""),
            asset_name=str(raw.get("asset_name") or ""),
            run_dir=str(raw.get("run_dir") or ""),
            foreground_port=port,
            extras={key: value for key, value in raw.items() if key not in known},
        )


def make_api_receipt(
    api_id: str,
    api_version: str,
    status: str,
    started_at: float,
    input_data: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    error_code: str = "",
    error: str = "",
    recovery_hint: str = "",
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    if status not in API_STATUSES:
        raise ApiContractError(f"invalid API status: {status}")
    return {
        "api_id": api_id,
        "api_version": api_version,
        "status": status,
        "input": input_data or {},
        "output": output or {},
        "elapsed_sec": round(time.perf_counter() - started_at, 3),
        "error_code": error_code,
        "error": error,
        "recovery_hint": recovery_hint,
        "artifacts": artifacts or [],
    }


def validate_receipt(
    receipt: Mapping[str, Any],
    *,
    api_id: str,
    api_version: str,
) -> dict[str, Any]:
    """Validate the stable receipt shape at the API boundary.

    Handlers may add fields, but the common fields are always present and have
    predictable types.  This keeps MCP/Worker/Workflow consumers on one
    contract instead of each caller guessing which receipt variant it got.
    """
    if not isinstance(receipt, Mapping):
        raise ApiContractError("API handler must return a receipt mapping")
    missing = sorted(_REQUIRED_RECEIPT_FIELDS - set(receipt))
    if missing:
        raise ApiContractError(f"API receipt missing fields {missing}: {api_id}")
    result = dict(receipt)
    result["api_id"] = api_id
    result["api_version"] = api_version
    if result["status"] not in API_STATUSES:
        raise ApiContractError(f"invalid API receipt status: {result['status']}")
    if not isinstance(result["input"], dict) or not isinstance(result["output"], dict):
        raise ApiContractError(f"API receipt input/output must be objects: {api_id}")
    if not isinstance(result["artifacts"], list):
        raise ApiContractError(f"API receipt artifacts must be a list: {api_id}")
    return result
