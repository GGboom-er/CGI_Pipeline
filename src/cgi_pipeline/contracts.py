"""Host-safe capability metadata, invocation, and receipt contracts."""

from __future__ import annotations

import re
import time
import math
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from .contract_values import validate_input_definition, validate_input_value, validate_json_value


class ApiContractError(ValueError):
    """Raised when a capability manifest or invocation is invalid."""


API_STATUSES = {
    "SUCCESS", "PARTIAL", "ERROR", "BLOCKED", "NEEDS_ATTENTION",
    "AUDIT_FAILED", "TIMEOUT", "CANCELLED",
}

# Framework adapters may supplement output with these fields. They are optional;
# a capability's own, more specific field description takes precedence.
FRAMEWORK_OUTPUT_FIELDS = {
    "summary": "Optional framework operation summary; present when the adapter supplies one.",
    "items": "Optional framework item records; present when the adapter supplies them.",
}


def validate_batch_result(result: Any, requested: int, atomic: bool) -> dict[str, Any]:
    """Validate a host batch envelope without rebuilding or rerunning its results."""
    if not isinstance(result, dict) or result.get('status') not in ('SUCCESS', 'ERROR'):
        raise ApiContractError('invalid batch response status')
    results = result.get('results')
    if not isinstance(results, list) or any(
        not isinstance(item, dict) or not isinstance(item.get('status'), str)
        or item['status'] not in API_STATUSES for item in results
    ):
        raise ApiContractError('invalid batch response results')
    if requested == 0 and result['status'] == 'ERROR' and result.get('error_code') == 'BATCH_EMPTY' and not results:
        return result
    if (type(result.get('requested')) is not int or result['requested'] != requested
            or type(result.get('completed')) is not int
            or result['completed'] != len(results) or not 0 < len(results) <= requested):
        raise ApiContractError('invalid batch response counts')
    if type(result.get('atomic')) is not bool or result['atomic'] != atomic:
        raise ApiContractError('invalid batch response atomic policy')
    if type(result.get('rolled_back')) is not bool:
        raise ApiContractError('invalid batch response rollback flag')
    full_success = len(results) == requested and all(item['status'] == 'SUCCESS' for item in results)
    if (result['status'] == 'SUCCESS') != full_success:
        raise ApiContractError('invalid batch response aggregate status')
    if not atomic and len(results) != requested:
        raise ApiContractError('non-atomic batch response must include every call')
    if atomic and (any(item['status'] != 'SUCCESS' for item in results[:-1])
                   or (len(results) < requested and results[-1]['status'] == 'SUCCESS')):
        raise ApiContractError('atomic batch response must stop at its first failure')
    if result['rolled_back'] and (not atomic or result['status'] == 'SUCCESS'):
        raise ApiContractError('invalid batch response rollback outcome')
    return result


_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_EXECUTORS = {"maya", "blender", "ue", "pipeline"}
_ACCESS = {"read", "write", "destructive"}
_EXECUTION_MODES = {"local", "foreground", "background"}
_MODES = {"inspect", "preview", "apply"}
_SURFACES = {"maya", "blender", "ue", "python", "service"}
_EFFECTS = {
    "scene_read", "scene_write", "file_read", "file_write",
    "host_state_write", "code_execute", "service_read", "unknown",
}
_STAGES = {"animation", "editor", "lighting", "lookdev", "modeling", "pipeline", "rigging"}
# Extend these manifest facets explicitly here; aliases do not create parallel tags.
_TARGETS = {
    "alembic", "animation", "animation_curve", "asset", "attribute", "audit",
    "blend_shape", "constraint", "control", "controller", "current_time", "curves",
    "deformer", "dependency_graph", "expression", "file", "geometry", "guide_curve",
    "hair", "hierarchy", "host_runtime", "host_session", "ik_handle", "joint",
    "keyframes", "level_actor", "material", "measurement", "mesh", "mesh_component",
    "metadata", "node_tree", "normal", "object_property", "pose", "python", "reference",
    "report", "rig", "scene", "scene_file", "scene_object", "selection", "shape",
    "simulation", "skin_cluster", "skin_weights", "tangent_types", "texture",
    "timeline", "transform", "ue_editor", "uv_set", "viewport",
}
_TARGET_QUERY_ALIASES = {"joints": "joint", "anim_curve": "animation_curve", "animation_curves": "animation_curve"}
# Public annotations derive from the same facets used to validate manifests.
Executor = Literal[tuple(sorted(_EXECUTORS))]
Access = Literal[tuple(sorted(_ACCESS))]
Surface = Literal[tuple(sorted(_SURFACES))]
ExecutionMode = Literal[tuple(sorted(_EXECUTION_MODES))]
Mode = Literal[tuple(sorted(_MODES))]
Effect = Literal[tuple(sorted(_EFFECTS))]


def normalize_target_query(targets):
    """Keep legacy query spellings compatible while manifests use canonical terms."""
    return None if targets is None else [
        _TARGET_QUERY_ALIASES.get(value, value) if isinstance(value, str) else value
        for value in targets
    ]


def validate_catalog_facets(executor=None, access=None, surfaces=None, *,
                            execution_modes=None, modes=None, effects=None,
                            stages=None, targets=None) -> None:
    for name, values, allowed in (
        ("executor", [] if executor is None else [executor], _EXECUTORS),
        ("access", [] if access is None else [access], _ACCESS),
        ("surfaces", surfaces or [], _SURFACES),
        ("execution_modes", execution_modes or [], _EXECUTION_MODES),
        ("modes", modes or [], _MODES),
        ("effects", effects or [], _EFFECTS),
        ("stages", stages or [], _STAGES),
        ("targets", targets or [], _TARGETS),
    ):
        for value in values:
            if not isinstance(value, str) or value not in allowed:
                raise ApiContractError(f"invalid {name}={value!r}; choose one of {sorted(allowed)}")

_IMPLEMENTATION_STATUSES = {"placeholder", "implemented"}
_VERIFICATION_LEVELS = {"untested", "contract_tested", "live_tested"}
_INPUT_DEFINITION_FIELDS = {"type", "description", "default", "required", "choices"}
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


def _validate_mode_binding(spec: Mapping[str, Any]) -> None:
    binding, api_id = spec.get("mode_binding"), spec["api_id"]
    if binding is None:
        if len(spec["modes"]) != 1:
            raise ApiContractError(f"multiple modes require mode_binding: {api_id}")
        return
    if not isinstance(binding, Mapping) or set(binding) != {"input", "cases"}:
        raise ApiContractError(f"mode_binding must contain input and cases: {api_id}")
    name, cases = binding["input"], binding["cases"]
    inputs = spec.get("inputs", {})
    if not isinstance(inputs, Mapping) or not isinstance(name, str) or name not in inputs:
        raise ApiContractError(f"mode_binding input must name a registered input: {api_id}")
    definition = inputs[name]
    kind = definition.get("type")
    if kind in ("boolean", "bool"):
        domain = [True, False]
    elif kind in ("string", "str"):
        domain = definition.get("choices")
        if not isinstance(domain, list) or not domain or any(type(v) is not str for v in domain):
            raise ApiContractError(f"mode_binding string input needs explicit choices: {api_id}")
    else:
        raise ApiContractError(f"mode_binding input must be string or boolean: {api_id}")
    if not definition.get("required") and "default" not in definition:
        raise ApiContractError(f"mode_binding input needs required or default: {api_id}")
    if not isinstance(cases, list) or not cases:
        raise ApiContractError(f"mode_binding cases must be a non-empty list: {api_id}")
    seen, mapped = set(), set()
    for case in cases:
        if not isinstance(case, Mapping) or set(case) != {"value", "mode"}:
            raise ApiContractError(f"mode_binding case must contain value and mode: {api_id}")
        value, mode = case["value"], case["mode"]
        if not any(type(value) is type(item) and value == item for item in domain):
            raise ApiContractError(f"mode_binding case outside typed input choices: {api_id}")
        token = (type(value), value)
        if token in seen:
            raise ApiContractError(f"duplicate mode_binding case: {api_id}")
        if not isinstance(mode, str) or mode not in spec["modes"]:
            raise ApiContractError(f"mode_binding case has unsupported mode: {api_id}")
        seen.add(token)
        mapped.add(mode)
    if len(seen) != len(domain) or mapped != set(spec["modes"]):
        raise ApiContractError(f"mode_binding must cover input choices and modes: {api_id}")
    if "default" in definition:
        resolve_mode(spec, {name: definition["default"]})


def resolve_mode(spec: Mapping[str, Any], values: Mapping[str, Any]) -> str:
    """Resolve operation intent from typed manifest cases, preserving business inputs."""
    binding = spec.get("mode_binding")
    if binding is None:
        if len(spec["modes"]) == 1:
            return spec["modes"][0]
        raise ApiContractError("multiple modes require mode_binding")
    name = binding["input"]
    if name in values:
        for case in binding["cases"]:
            if type(values[name]) is type(case["value"]) and values[name] == case["value"]:
                return case["mode"]
    raise ApiContractError(f"invalid mode_binding input {name}={values.get(name)!r}")


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
    strategy = result.get('execution_strategy', 'worker')
    if not isinstance(strategy, str) or strategy not in {'worker', 'control'}:
        raise ApiContractError(f"invalid execution_strategy for {api_id}: {strategy!r}")
    if strategy == 'control' and (result['executor'] != 'pipeline'
            or result['execution_modes'] != ['background']
            or 'service' not in result['surfaces']):
        raise ApiContractError(f"control strategy requires a background pipeline service API: {api_id}")

    result["stages"] = _string_list(result, "stages", api_id)
    result["targets"] = _string_list(result, "targets", api_id)
    result["execution_modes"] = _string_list(result, "execution_modes", api_id)
    result["surfaces"] = _string_list(result, "surfaces", api_id)
    result["modes"] = _string_list(result, "modes", api_id)
    validate_catalog_facets(stages=result["stages"], targets=result["targets"])
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
    if not isinstance(result["effects"], Mapping):
        raise ApiContractError(f"effects must be a mapping for {api_id}")
    implementation = result.get("implementation", {"status": "unknown"})
    if not isinstance(implementation, Mapping):
        raise ApiContractError(f"implementation must be a mapping: {api_id}")
    implementation_status = implementation.get("status", "unknown")
    if implementation_status not in _IMPLEMENTATION_STATUSES | {"unknown"}:
        raise ApiContractError(
            f"invalid implementation status for {api_id}: {implementation_status!r}"
        )
    result["implementation"] = dict(implementation)
    verification = result.get("verification", {"level": "untested", "evidence": []})
    if not isinstance(verification, Mapping):
        raise ApiContractError(f"verification must be a mapping: {api_id}")
    verification_level = verification.get("level", "untested")
    if verification_level not in _VERIFICATION_LEVELS:
        raise ApiContractError(
            f"invalid verification level for {api_id}: {verification_level!r}"
        )
    evidence = verification.get("evidence", [])
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ApiContractError(f"verification evidence must be a string list: {api_id}")
    result["verification"] = {**dict(verification), "evidence": list(evidence)}
    for field_name in ("inputs", "outputs", "statuses", "errors", "help", "tests"):
        if not isinstance(result[field_name], (dict, list, str)):
            raise ApiContractError(f"invalid {field_name} for {api_id}")
    if not isinstance(result["inputs"], Mapping):
        raise ApiContractError(f"inputs must be a mapping for {api_id}")
    if not isinstance(result["outputs"], Mapping):
        raise ApiContractError(f"outputs must map actual top-level field names to descriptions: {api_id}")
    if isinstance(result["inputs"], Mapping):
        for input_name, definition in result["inputs"].items():
            if not isinstance(definition, Mapping):
                raise ApiContractError(
                    f"input definition must be a mapping for {api_id}.{input_name}"
                )
            unknown_fields = sorted(set(definition) - _INPUT_DEFINITION_FIELDS)
            if unknown_fields:
                raise ApiContractError(
                    f"unknown input definition fields for {api_id}.{input_name}: "
                    f"{unknown_fields}; quote YAML descriptions containing commas"
                )
            if "description" in definition and not isinstance(definition["description"], str):
                raise ApiContractError(
                    f"input description must be a string for {api_id}.{input_name}"
                )
    for field_name in ("effects", "outputs", "errors"):
        value = result[field_name]
        if isinstance(value, Mapping) and not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value.items()
        ):
            raise ApiContractError(
                f"{field_name} entries must map strings to strings for {api_id}; "
                "quote YAML values containing commas"
            )
    if any(not key.strip() or not description.strip() for key, description in result["outputs"].items()):
        raise ApiContractError(f"outputs field names and descriptions must be non-empty: {api_id}")
    validate_catalog_facets(effects=result["effects"])
    _validate_mode_binding(result)
    for name, definition in result["inputs"].items():
        validate_input_definition(f"{api_id}.{name}", definition, error_type=ApiContractError)
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
        validate_input_definition(name, definition, error_type=ApiContractError)
        if definition.get("required") and name not in values:
            raise ApiContractError(f"missing required API input: {name}")
        if name not in values:
            if "default" in definition:
                values[name] = deepcopy(definition["default"])
            else:
                continue
        validate_input_value(name, definition, values[name], error_type=ApiContractError)
    if spec.get("mode_binding") is not None:
        resolve_mode(spec, values)
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
    if not isinstance(result["status"], str) or result["status"] not in API_STATUSES:
        raise ApiContractError(f"invalid API receipt status: {result['status']}")
    if not isinstance(result["input"], dict) or not isinstance(result["output"], dict):
        raise ApiContractError(f"API receipt input/output must be objects: {api_id}")
    if not isinstance(result["artifacts"], list) or any(not isinstance(item, str) for item in result["artifacts"]):
        raise ApiContractError(f"API receipt artifacts must be a string array: {api_id}")
    for field_name in ("error_code", "error", "recovery_hint"):
        if not isinstance(result[field_name], str):
            raise ApiContractError(f"API receipt {field_name} must be a string: {api_id}")
    elapsed = result["elapsed_sec"]
    if (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
            or (isinstance(elapsed, float) and not math.isfinite(elapsed)) or elapsed < 0):
        raise ApiContractError(f"API receipt elapsed_sec must be a finite non-negative number: {api_id}")
    validate_json_value(result, f"API receipt {api_id}", error_type=ApiContractError)
    return result
