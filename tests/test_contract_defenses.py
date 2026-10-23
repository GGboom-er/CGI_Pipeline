"""Reject malformed boundaries before a handler or JSON transport consumes them."""

from copy import deepcopy
import math
import time

import pytest

from cgi_pipeline.contracts import (
    ApiContractError, make_api_receipt, validate_params, validate_receipt, validate_spec,
)
from test_manifest_description_validation import _spec


@pytest.mark.parametrize("kind", ["strnig", ["string", "strnig"], [], ["integer", 1]])
def test_unknown_input_types_fail_at_definition_and_invocation(kind):
    spec = _spec()
    spec["inputs"] = {"value": {"type": kind}}
    with pytest.raises(ApiContractError, match="type"):
        validate_spec(spec)
    with pytest.raises(ApiContractError, match="type"):
        validate_params(spec, {"value": "probe"})


@pytest.mark.parametrize("definition", [
    {"type": "integer", "default": "3"},
    {"type": "boolean", "default": 1},
    {"type": "string", "choices": ["a"], "default": "b"},
    {"type": "number", "default": math.nan},
])
def test_defaults_use_the_same_validation_as_explicit_values(definition):
    spec = _spec()
    spec["inputs"] = {"value": definition}
    with pytest.raises(ApiContractError):
        validate_spec(spec)
    for supplied in ({}, {"value": definition["default"]}):
        with pytest.raises(ApiContractError):
            validate_params(spec, supplied)


def test_mutable_defaults_are_independent_for_each_invocation():
    spec = {"inputs": {"items": {"type": "array", "default": [{"name": "a"}]}}}
    first = validate_params(spec, {})
    first["items"][0]["name"] = "mutated"
    assert validate_params(spec, {}) == {"items": [{"name": "a"}]}
    assert spec["inputs"]["items"]["default"] == [{"name": "a"}]


@pytest.mark.parametrize("choice,value", [(True, 1), (1, True), (False, 0)])
def test_choices_do_not_equate_booleans_and_integers(choice, value):
    spec = {"inputs": {"value": {"type": ["boolean", "integer"], "choices": [choice]}}}
    with pytest.raises(ApiContractError, match="choose"):
        validate_params(spec, {"value": value})


def _receipt():
    return make_api_receipt("legacy.short", "1.0.0", "SUCCESS", time.perf_counter())


@pytest.mark.parametrize("field,value", [
    ("status", []), ("error_code", 42), ("error", None), ("recovery_hint", {}),
    ("elapsed_sec", "1"), ("elapsed_sec", True), ("elapsed_sec", -1),
    ("elapsed_sec", math.nan), ("elapsed_sec", math.inf),
    ("artifacts", [object()]), ("output", {"number": math.nan}),
    ("output", {1: "non-string key"}), ("output", {"nested": (object(),)}),
])
def test_receipt_rejects_bad_scalars_and_non_json_values(field, value):
    receipt = _receipt()
    receipt[field] = value
    with pytest.raises(ApiContractError):
        validate_receipt(receipt, api_id="maya.scene.example", api_version="1.0.1")


def test_receipt_rejects_cycles_with_a_contract_error():
    receipt = _receipt()
    receipt["output"]["cycle"] = receipt["output"]
    with pytest.raises(ApiContractError, match="cycl"):
        validate_receipt(receipt, api_id="maya.scene.example", api_version="1.0.1")


def test_json_compatible_tuple_and_legacy_identity_still_work():
    receipt = _receipt()
    receipt["output"] = {"position": (1.0, 2.0, 3.0)}
    result = validate_receipt(receipt, api_id="maya.scene.example", api_version="1.0.1")
    assert result["api_id"] == "maya.scene.example"
    assert result["api_version"] == "1.0.1"
    assert result["output"]["position"] == (1.0, 2.0, 3.0)


def test_finite_integer_elapsed_does_not_overflow_a_float_conversion():
    receipt = _receipt()
    receipt["elapsed_sec"] = 10 ** 400
    assert validate_receipt(receipt, api_id="maya.scene.example", api_version="1.0.1")["elapsed_sec"] == 10 ** 400


@pytest.mark.parametrize("outputs", [["scene"], "scene", {"result": ""}])
def test_outputs_require_named_nonempty_field_descriptions(outputs):
    spec = _spec()
    spec["outputs"] = deepcopy(outputs)
    with pytest.raises(ApiContractError, match="outputs"):
        validate_spec(spec)


def test_registered_outputs_describe_real_fields_instead_of_old_ports():
    from cgi_pipeline.catalog import capability_help

    saved = capability_help("maya.asset.save_scene")["outputs"]
    assert "output_path" in saved and "scene" not in saved
    executed = capability_help("blender.asset.blender_exec_code")["outputs"]
    assert "result" in executed and "blend" not in executed
    assert set(capability_help("maya.asset.maya_conform_normals")["outputs"]) == {"summary", "items"}
    assert set(capability_help("maya.xgen.scene")["outputs"]) == {"groups", "start", "end", "fps", "scene", "unit"}
    assert "result" not in capability_help("maya.xgen.export")["outputs"]


def test_framework_descriptions_are_explicit_per_capability_and_do_not_inject_fields():
    from cgi_pipeline.contracts import FRAMEWORK_OUTPUT_FIELDS

    spec = _spec()
    original = deepcopy(spec)
    spec["outputs"]["summary"] = FRAMEWORK_OUTPUT_FIELDS["summary"]
    result = validate_spec(spec)
    assert result["outputs"]["summary"] == FRAMEWORK_OUTPUT_FIELDS["summary"]
    assert "items" in result["outputs"]  # This API owns its own specific items field.
    assert original["outputs"] == {"items": "Rows."}
    empty = _spec()
    empty["outputs"] = {}
    assert validate_spec(empty)["outputs"] == {}
