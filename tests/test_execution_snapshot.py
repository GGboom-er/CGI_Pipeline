"""An execution boundary must not mix contracts across catalog publications."""

from copy import deepcopy
import sys
import time
from types import ModuleType
from unittest.mock import Mock

import pytest

from cgi_pipeline import api, catalog, execution
from cgi_pipeline.contracts import ApiContractError, make_api_receipt


@pytest.fixture
def generations(monkeypatch):
    spec = catalog.get_capability("maya.scene.selection.get")
    api_id = spec["api_id"]
    module = ModuleType("execution_snapshot_fixture")
    seen = []

    def handler(name):
        def run(params, context):
            seen.append(name)
            return make_api_receipt(api_id, spec["version"], "SUCCESS", time.perf_counter(),
                                    input_data=params, output={"generation": name})
        return run

    old = deepcopy(spec)
    new = deepcopy(spec)
    old["handler"] = "execution_snapshot_fixture:old"
    new["handler"] = "execution_snapshot_fixture:new"
    module.old, module.new = handler("old"), handler("new")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return api_id, ({api_id: old}, "old"), ({api_id: new}, "new"), seen


def test_api_uses_validated_spec_to_resolve_handler(generations, monkeypatch):
    api_id, old, new, seen = generations
    read = Mock(side_effect=[old, new])
    monkeypatch.setattr(catalog, "_snapshot", read)

    result = execution.execute_api(api_id, {}, {"execution_mode": "local", "host": "maya"})

    assert result["status"] == "SUCCESS"
    assert seen == ["old"]
    assert read.call_count == 1


@pytest.mark.parametrize("atomic", [True, False])
def test_batch_pins_one_generation_for_every_call(generations, monkeypatch, atomic):
    api_id, old, new, seen = generations
    read = Mock(side_effect=[old, new])
    monkeypatch.setattr(catalog, "_snapshot", read)

    result = execution.execute_batch(
        [{"api_id": api_id}, {"api_id": api_id}], atomic=atomic,
        context={"execution_mode": "local", "host": "maya"})

    assert result["status"] == "SUCCESS"
    assert seen == ["old", "old"]
    assert read.call_count == 1


def test_local_sdk_passes_its_surface_validation_snapshot(generations, monkeypatch):
    api_id, old, new, seen = generations
    read = Mock(side_effect=[old, new])
    monkeypatch.setattr(catalog, "_snapshot", read)

    assert api.execute_local(api_id, {}, host="maya")["status"] == "SUCCESS"
    assert seen == ["old"]
    assert read.call_count == 1


def test_caller_snapshot_is_reused_without_another_catalog_query(generations, monkeypatch):
    api_id, old, _, seen = generations
    monkeypatch.setattr(catalog, "_snapshot", Mock(side_effect=AssertionError("unexpected resample")))
    context = {"execution_mode": "local", "host": "maya"}

    assert execution.execute_api(api_id, {}, context, catalog_snapshot=old)["status"] == "SUCCESS"
    assert execution.execute_batch([{"api_id": api_id}], context=context,
                                   catalog_snapshot=old)["status"] == "SUCCESS"
    assert seen == ["old", "old"]


def test_final_host_rejects_catalog_changed_after_submission(generations):
    api_id, _, new, seen = generations
    result = execution.execute_api(api_id, {}, {
        "execution_mode": "local", "host": "maya", "expected_catalog_version": "old",
    }, catalog_snapshot=new)

    assert result["status"] == "ERROR"
    assert result["error_code"] == "CATALOG_VERSION_STALE"
    assert result["output"]["catalog_version"] == "new"
    assert seen == []


@pytest.mark.parametrize("atomic", [True, False])
def test_stale_batch_never_opens_undo_or_calls_handler(generations, atomic):
    api_id, _, new, seen = generations
    cmds = Mock()
    result = execution.execute_batch([{"api_id": api_id}, {"api_id": api_id}], atomic=atomic,
                                    context={"cmds_module": cmds, "expected_catalog_version": "old"},
                                    catalog_snapshot=new)

    assert result["status"] == "ERROR"
    assert all(item["error_code"] == "CATALOG_VERSION_STALE" for item in result["results"])
    assert result["completed"] == (1 if atomic else 2)
    assert result["rolled_back"] is False
    cmds.undoInfo.assert_not_called()
    cmds.undo.assert_not_called()
    assert seen == []


def test_invalid_catalog_returns_error_before_any_handler(monkeypatch):
    monkeypatch.setattr(catalog, "_snapshot", Mock(side_effect=ApiContractError("broken catalog")))
    handler = Mock()
    monkeypatch.setattr(execution, "load_handler", handler)

    result = execution.execute_api("maya.scene.selection.get")

    assert result["error_code"] == "API_CONTRACT_ERROR"
    handler.assert_not_called()


@pytest.mark.parametrize("chain", [False, True])
def test_submission_rejects_version_changed_after_mcp_guard(generations, monkeypatch, chain):
    from cgi_pipeline.server import internals

    api_id, _, new, seen = generations
    monkeypatch.setattr(catalog, "_snapshot", Mock(return_value=new))
    worker = Mock(side_effect=AssertionError("must not start worker"))
    monkeypatch.setattr(internals, "_ensure_worker", worker)

    if chain:
        result = internals._submit_chain({
            "api_chain": [{"api_id": api_id, "parameters": {}}],
            "api_context": {"expected_catalog_version": "old"},
        })
    else:
        result = internals._submit_api_to_celery(api_id, {
            "params": {}, "context": {"expected_catalog_version": "old"},
        })

    assert result["error_code"] == "CATALOG_VERSION_STALE"
    worker.assert_not_called()
    assert seen == []


def test_chain_schema_uses_caller_snapshot_for_all_defaults(generations, monkeypatch):
    from cgi_pipeline.core.schemas import ApiChainPayloadSchema

    api_id, old, new, _ = generations
    read = Mock(return_value=new)
    monkeypatch.setattr(catalog, "_snapshot", read)
    parsed = ApiChainPayloadSchema.model_validate({
        "task_id": "snapshot-chain", "api_chain": [{"api_id": api_id}, {"api_id": api_id}],
        "api_context": {"expected_catalog_version": "old"},
    }, context={"catalog_snapshot": old})

    assert len(parsed.api_chain) == 2
    read.assert_not_called()


@pytest.mark.parametrize("chain", [False, True])
def test_schema_rejects_stale_version_before_normalizing(generations, monkeypatch, chain):
    from cgi_pipeline.core.schemas import ApiChainPayloadSchema, ApiPayloadSchema

    api_id, _, new, _ = generations
    monkeypatch.setattr(catalog, "_snapshot", Mock(return_value=new))
    payload = {"api_context": {"expected_catalog_version": "old"}}
    if chain:
        payload.update(task_id="stale-chain", api_chain=[{"api_id": api_id}])
        schema = ApiChainPayloadSchema
    else:
        payload["api_id"] = api_id
        schema = ApiPayloadSchema
    with pytest.raises(ValueError, match="CATALOG_VERSION_STALE"):
        schema.model_validate(payload)
