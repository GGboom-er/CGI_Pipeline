"""Execution policy is explicit catalog data, not a private API-name list."""
from copy import deepcopy

import pytest

from cgi_pipeline.catalog import get_capability
from cgi_pipeline.contracts import ApiContractError, validate_spec


def test_existing_manifests_keep_worker_strategy_without_new_required_field():
    spec = get_capability('pipeline.session.discover')
    spec.pop('execution_strategy', None)
    assert validate_spec(spec).get('execution_strategy', 'worker') == 'worker'


@pytest.mark.parametrize('strategy', [None, '', False, [], 'threads', 'service'])
def test_unknown_execution_strategy_is_rejected(strategy):
    spec = get_capability('pipeline.session.discover')
    spec['execution_strategy'] = strategy
    with pytest.raises(ApiContractError, match='execution_strategy'):
        validate_spec(spec)


def test_pipeline_service_control_strategy_is_explicit_and_preserved():
    spec = get_capability('pipeline.session.discover')
    spec['execution_strategy'] = 'control'
    assert validate_spec(spec)['execution_strategy'] == 'control'


@pytest.mark.parametrize('api_id', ['maya.asset.exec_code', 'blender.asset.blender_exec_code'])
def test_host_operations_cannot_declare_service_control_strategy(api_id):
    spec = deepcopy(get_capability(api_id))
    spec['execution_strategy'] = 'control'
    with pytest.raises(ApiContractError, match='control'):
        validate_spec(spec)


@pytest.mark.parametrize('action', ['status', 'release'])
def test_direct_runtime_handler_receipt_matches_registered_version(action, monkeypatch):
    from types import SimpleNamespace
    from cgi_pipeline.capabilities.pipeline.runtime import handler
    from cgi_pipeline.contracts import ApiContext

    control = SimpleNamespace(**{action: lambda params: {'status': 'SUCCESS'}})
    monkeypatch.setattr(handler, 'RuntimeControl', lambda: control)
    result = getattr(handler, action)({}, ApiContext(executor='pipeline'))
    assert result['api_version'] == get_capability(f'pipeline.runtime.{action}')['version']
