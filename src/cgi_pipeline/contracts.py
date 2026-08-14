"""Host-safe capability metadata, invocation, and receipt contracts."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping


class ApiContractError(ValueError):
    """Raised when a capability manifest or invocation is invalid."""


API_STATUSES = {
    "SUCCESS", "PARTIAL", "ERROR", "BLOCKED", "NEEDS_ATTENTION",
    "TIMEOUT", "CANCELLED",
}
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_EXECUTORS = {"maya", "blender", "ue", "pipeline"}
_ACCESS = {"read", "write", "destructive"}
_EXECUTION_MODES = {"local", "foreground", "background"}
_MODES = {"inspect", "preview", "apply"}
_SURFACES = {"maya", "blender", "ue", "python", "service"}
_REQUIRED_SPEC_FIELDS = {
    "api_id", "version", "executor", "capability", "operation", "stages",
    "targets", "access", "effects", "execution_modes", "surfaces", "modes",
    "inputs", "preconditions", "idempotent", "outputs", "statuses", "errors",
    "recovery", "help", "tests", "handler",
}
_REQUIRED_RECEIPT_FIELDS = {
    "api_id", "api_version", "status", "input", "output", "elapsed_sec",
    "error_code", "error", "recovery_hint", "artifacts",
}


def _string_list(spec: Mapping[str, Any], field_name: str, api_id: str) -> list[str]:
    value = spec.get(field_name)
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise ApiContractError(f"{field_name} must be a non-empty string list: {api_id}")
    return list(value)


def validate_spec(spec: Mapping[str, Any], source: str = "") -> dict[str, Any]:
    """Validate and normalize one capability manifest."""
    if not isinstance(spec, Mapping):
        raise ApiContractError(f"capability manifest must be a mapping: {source}")
    missing = sorted(_REQUIRED_SPEC_FIELDS - set(spec))
    if missing:
        raise ApiContractError(f"capability manifest missing {missing}: {source}")

    result = dict(spec)
    api_id = result["api_id"]
    if not isinstance(api_id, str) or not _ID_RE.fullmatch(api_id):
        raise ApiContractError(f"invalid api_id: {api_id!r}")
    for field_name in ("capability", "operation"):
        value = result[field_name]
        if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
            raise ApiContractError(f"invalid {field_name} for {api_id}: {value!r}")
    if result["executor"] not in _EXECUTORS:
        raise ApiContractError(f"invalid executor for {api_id}: {result['executor']!r}")
    if result["access"] not in _ACCESS:
        raise ApiContractError(f"invalid access for {api_id}: {result['access']!r}")

    result["stages"] = _string_list(result, "stages", api_id)
    result["targets"] = _string_list(result, "targets", api_id)
    result["execution_modes"] = _string_list(result, "execution_modes", api_id)
    result["surfaces"] = _string_list(result, "surfaces", api_id)
    result["modes"] = _string_list(result, "modes", api_id)
    invalid = set(result["execution_modes"]) - _EXECUTION_MODES
    if invalid:
        raise ApiContractError(f"invalid execution_modes for {api_id}: {sorted(invalid)}")
    invalid = set(result["surfaces"]) - _SURFACES
    if invalid:
        raise ApiContractError(f"invalid surfaces for {api_id}: {sorted(invalid)}")
    invalid = set(result["modes"]) - _MODES
    if invalid:
        raise ApiContractError(f"invalid modes for {api_id}: {sorted(invalid)}")
    if not isinstance(result["idempotent"], bool):
        raise ApiContractError(f"idempotent must be boolean: {api_id}")
    if not isinstance(result["handler"], str) or ":" not in result["handler"]:
        raise ApiContractError(f"handler must be module:function: {api_id}")
    if not isinstance(result["effects"], (dict, list, str)):
        raise ApiContractError(f"invalid effects for {api_id}")
    for field_name in ("inputs", "outputs", "statuses", "errors", "help", "tests"):
        if not isinstance(result[field_name], (dict, list, str)):
            raise ApiContractError(f"invalid {field_name} for {api_id}")
    if isinstance(result["statuses"], list):
        invalid = set(result["statuses"]) - API_STATUSES
        if invalid:
            raise ApiContractError(f"invalid statuses for {api_id}: {sorted(invalid)}")
    for field_name in ("preconditions", "recovery"):
        if not isinstance(result[field_name], (list, dict, str)):
            raise ApiContractError(f"invalid {field_name} for {api_id}")
    result["source"] = source
    return result


def validate_params(spec: Mapping[str, Any], params: Mapping[str, Any] | None) -> dict[str, Any]:
    values = dict(params or {})
    inputs = spec.get("inputs", {})
    if not isinstance(inputs, Mapping):
        return values
    unknown = sorted(set(values) - set(inputs))
    if unknown:
        raise ApiContractError(f"unknown API inputs: {', '.join(unknown)}")
    for name, raw_definition in inputs.items():
        definition = raw_definition if isinstance(raw_definition, Mapping) else {}
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
        value = values[name]
        valid = {
            "string": isinstance(value, str), "str": isinstance(value, str),
            "array": isinstance(value, list), "list": isinstance(value, list),
            "object": isinstance(value, dict), "dict": isinstance(value, dict),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "int": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "float": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool), "bool": isinstance(value, bool),
        }
        if expected in valid and not valid[expected]:
            raise ApiContractError(f"{name} must be a {expected}")
    return values


@dataclass(frozen=True)
class ApiContext:
    executor: str
    execution_mode: str = "background"
    source_path: str = ""
    project: str = ""
    asset_name: str = ""
    run_dir: str = ""
    foreground_port: int | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, context: Mapping[str, Any] | None, executor: str) -> "ApiContext":
        raw = dict(context or {})
        port = raw.get("foreground_port")
        if port not in (None, ""):
            try:
                port = int(port)
            except (TypeError, ValueError) as exc:
                raise ApiContractError("foreground_port must be an integer") from exc
        known = {"executor", "execution_mode", "source_path", "project", "asset_name", "run_dir", "foreground_port"}
        return cls(
            executor=executor,
            execution_mode=str(raw.get("execution_mode") or "background"),
            source_path=str(raw.get("source_path") or ""),
            project=str(raw.get("project") or ""),
            asset_name=str(raw.get("asset_name") or ""),
            run_dir=str(raw.get("run_dir") or ""),
            foreground_port=port,
            extras={key: value for key, value in raw.items() if key not in known},
        )


def make_api_receipt(api_id: str, api_version: str, status: str, started_at: float,
                     input_data: dict[str, Any] | None = None,
                     output: dict[str, Any] | None = None, error_code: str = "",
                     error: str = "", recovery_hint: str = "",
                     artifacts: list[str] | None = None) -> dict[str, Any]:
    if status not in API_STATUSES:
        raise ApiContractError(f"invalid API status: {status}")
    return {
        "api_id": api_id, "api_version": api_version, "status": status,
        "input": input_data or {}, "output": output or {},
        "elapsed_sec": round(time.perf_counter() - started_at, 3),
        "error_code": error_code, "error": error,
        "recovery_hint": recovery_hint, "artifacts": artifacts or [],
    }


def validate_receipt(receipt: Mapping[str, Any], *, api_id: str, api_version: str) -> dict[str, Any]:
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
        raise ApiContractError(f"API receipt artifacts must be an array: {api_id}")
    return result
