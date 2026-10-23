from __future__ import annotations

import pytest

from cgi_pipeline.contracts import ApiContractError, validate_spec


def _spec() -> dict:
    return {
        "api_id": "maya.scene.example",
        "version": "1.0.0",
        "executor": "maya",
        "capability": "scene.example",
        "operation": "inspect",
        "stages": ["rigging"],
        "targets": ["scene"],
        "access": "read",
        "effects": {"scene_read": "Reads the scene."},
        "execution_modes": ["foreground"],
        "surfaces": ["maya"],
        "modes": ["inspect"],
        "inputs": {"max_items": {"type": "integer", "description": "Maximum items."}},
        "preconditions": [],
        "idempotent": True,
        "outputs": {"items": "Rows."},
        "statuses": ["SUCCESS", "ERROR"],
        "errors": {"EXAMPLE_FAILED": "Query failed."},
        "recovery": ["Retry."],
        "help": {"summary": "Example."},
        "tests": {"contract": "tests/test_example.py"},
        "handler": "example:run",
    }


def test_manifest_rejects_yaml_description_split_into_unknown_input_fields():
    spec = _spec()
    spec["inputs"]["max_items"]["hard limit 2000."] = None

    with pytest.raises(ApiContractError, match="quote YAML descriptions containing commas"):
        validate_spec(spec)


@pytest.mark.parametrize("field", ["effects", "outputs", "errors"])
def test_manifest_rejects_yaml_text_split_into_null_mapping_entries(field):
    spec = _spec()
    spec[field]["split text"] = None

    with pytest.raises(ApiContractError, match="quote YAML values containing commas"):
        validate_spec(spec)


@pytest.mark.parametrize("field,value", [
    ("targets", ["joints"]), ("targets", ["anim_curve"]),
    ("targets", ["animation_curves"]), ("effects", {"inspect": "Reads."}),
    ("effects", {"scene_wirte": "Writes."}), ("effects", []),
])
def test_manifest_rejects_noncanonical_facets(field, value):
    spec = _spec()
    spec[field] = value
    with pytest.raises(ApiContractError, match=field):
        validate_spec(spec)


def _bound_spec():
    spec = _spec()
    spec["modes"] = ["preview", "apply"]
    spec["inputs"] = {"dry_run": {"type": "boolean", "default": True}}
    spec["mode_binding"] = {"input": "dry_run", "cases": [
        {"value": True, "mode": "preview"}, {"value": False, "mode": "apply"},
    ]}
    return spec


@pytest.mark.parametrize("mutation", [
    lambda spec: spec.pop("mode_binding"),
    lambda spec: spec["mode_binding"].update(input="missing"),
    lambda spec: spec["mode_binding"]["cases"].pop(),
    lambda spec: spec["mode_binding"]["cases"].append({"value": True, "mode": "preview"}),
    lambda spec: spec["mode_binding"]["cases"][0].update(value="true"),
    lambda spec: spec["mode_binding"]["cases"][0].update(value=1),
    lambda spec: spec["mode_binding"]["cases"][0].update(mode="inspect"),
    lambda spec: spec["inputs"]["dry_run"].update(default="false"),
    lambda spec: spec["inputs"]["dry_run"].update(default=0),
])
def test_mode_binding_rejects_incomplete_or_untyped_contracts(mutation):
    spec = _bound_spec()
    mutation(spec)
    with pytest.raises(ApiContractError, match="mode_binding"):
        validate_spec(spec)


def test_typed_mode_cases_survive_json_roundtrip_and_empty_effects_are_valid():
    import json
    from cgi_pipeline.contracts import resolve_mode, validate_params

    spec = _bound_spec()
    spec["effects"] = {}
    validated = validate_spec(json.loads(json.dumps(spec)))
    assert resolve_mode(validated, validate_params(validated, {})) == "preview"
    assert resolve_mode(validated, validate_params(validated, {"dry_run": False})) == "apply"
    for value in ("true", "false", 0, 1, None):
        with pytest.raises(ApiContractError):
            validate_params(validated, {"dry_run": value})
