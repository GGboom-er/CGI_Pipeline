import time

import pytest

from cgi_pipeline.capabilities.runtime import execute_operation
from cgi_pipeline.contracts import ApiContext, ApiContractError, validate_receipt
from cgi_pipeline.core.receipt import make_receipt


def test_audit_failed_is_preserved_as_canonical_api_status():
    def implementation(_payload):
        return {
            "status": "AUDIT_FAILED",
            "error": "quality gate failed",
            "output": {"passed": False},
        }

    receipt = execute_operation(
        implementation,
        {},
        ApiContext(
            executor="maya",
            extras={"_api_id": "maya.test.audit", "_api_version": "1.0.0"},
        ),
    )

    assert receipt["status"] == "AUDIT_FAILED"
    assert receipt["error"] == "quality gate failed"
    assert receipt["output"]["passed"] is False


def _context():
    return ApiContext(executor="maya", extras={"_api_id": "maya.test.status", "_api_version": "1.0.0"})


def test_source_operation_error_code_and_details_survive_the_adapter():
    source = make_receipt(
        "maya_select_objects", "ERROR", time.time(),
        output={"restored": True, "conflicts": ["cameraChanged"]},
        error="Current view differs from the expected after-state",
        error_code="VIEW_RESTORE_CONFLICT", recovery_hint="Keep current view; inspect the conflict.",
    )
    receipt = execute_operation(lambda payload: source, {}, _context())
    assert receipt["status"] == "ERROR" and receipt["error_code"] == "VIEW_RESTORE_CONFLICT"
    assert receipt["error"] == source["error"]
    assert receipt["recovery_hint"] == source["recovery_hint"]
    assert receipt["output"]["conflicts"] == ["cameraChanged"]
    assert receipt["output"]["restored"] is True
    validate_receipt(receipt, api_id="maya.test.status", api_version="1.0.0")


@pytest.mark.parametrize("source", [
    {}, {"error_code": None}, {"error_code": ""}, {"error_code": "   "},
    {"error_code": 7}, {"error_code": False}, {"error_code": {}}, {"error_code": []},
])
def test_missing_empty_or_invalid_source_error_code_keeps_generic_fallback(source):
    receipt = execute_operation(lambda payload: {"status": "ERROR", "error": "failed", **source}, {}, _context())
    assert receipt["status"] == "ERROR" and receipt["error_code"] == "API_OPERATION_ERROR"
    assert receipt["error"] == "failed"
    validate_receipt(receipt, api_id="maya.test.status", api_version="1.0.0")


@pytest.mark.parametrize("source_status,canonical_status", [
    ("SUCCESS", "SUCCESS"), ("CHAIN_SUCCESS", "SUCCESS"), ("PARTIAL", "PARTIAL"),
    ("BLOCKED", "BLOCKED"), ("AUDIT_FAILED", "AUDIT_FAILED"),
    ("NEEDS_ATTENTION", "NEEDS_ATTENTION"), ("TIMEOUT", "TIMEOUT"), ("CANCELLED", "CANCELLED"),
])
def test_non_error_status_mapping_and_error_fields_retain_existing_semantics(source_status, canonical_status):
    source = {"status": source_status, "error_code": "DO_NOT_PROPAGATE", "error": "source detail", "output": {"ok": 1}}
    receipt = execute_operation(lambda payload: source, {}, _context())
    assert receipt["status"] == canonical_status and receipt["error_code"] == ""
    assert receipt["error"] == ("" if canonical_status == "SUCCESS" else "source detail")
    assert receipt["output"]["ok"] == 1


def test_implementation_exception_retains_generic_adapter_error():
    def raises(payload):
        raise RuntimeError("native operation raised")
    receipt = execute_operation(raises, {}, _context())
    assert receipt["status"] == "ERROR" and receipt["error_code"] == "API_OPERATION_ERROR"
    assert receipt["error"] == "native operation raised"


def test_non_mapping_operation_result_retains_contract_failure():
    with pytest.raises(ApiContractError, match="receipt mapping"):
        execute_operation(lambda payload: [], {}, _context())
