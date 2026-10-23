"""The development checker compiles fragments, variants, and repairs."""
import importlib.util
import json
from pathlib import Path

import pytest

from cgi_pipeline.core import workflow_engine


ROOT = Path(__file__).resolve().parents[1]


def _checker():
    spec = importlib.util.spec_from_file_location('pipeline_checker', ROOT / 'tools/check_pipeline_governance.py')
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    return module.check_workflows


def _write_flow(tmp_path, monkeypatch, change):
    fragment = {'steps': [{'step_id': 'one', 'api_id': 'valid.api', 'parameters': {}}]}
    raw = {'workflow_id': 'probe', 'parameters': {'enabled': {'type': 'boolean', 'default': False}},
           'steps_include': {'fragment': 'fragments/shared.json', 'variants': []}}
    change(raw, fragment)
    (tmp_path / 'fragments').mkdir()
    (tmp_path / 'fragments/shared.json').write_text(json.dumps(fragment))
    (tmp_path / 'probe.json').write_text(json.dumps(raw))
    monkeypatch.setattr(workflow_engine, '_WORKFLOWS_DIR', tmp_path)
    return tmp_path


@pytest.mark.parametrize('case', ['variant', 'fragment', 'repair', 'schema', 'all_rejected'])
def test_checker_rejects_bad_expanded_workflow_branches(tmp_path, monkeypatch, case):
    def change(raw, fragment):
        if case == 'variant':
            raw['steps_include']['variants'] = [{'when': {'enabled': True},
                'overrides': {'one': {'api_id': 'missing.api'}}}]
        elif case == 'fragment':
            fragment['steps'][0]['api_id'] = 'missing.api'
        elif case == 'repair':
            fragment['steps'][0]['failure_policy'] = {'action': 'repair_then_retry',
                'repair_step': {'api_id': 'missing.api', 'parameters': {}}}
        elif case == 'schema':
            raw['steps_include']['variants'] = [{'when': {'enabled': True}, 'select': ['absent']}]
        else:
            raw['reject'] = [{'when': {'enabled': enabled}, 'error': 'unavailable'} for enabled in [True, False]]
    directory = _write_flow(tmp_path, monkeypatch, change)
    issues, _ = _checker()(directory, {'valid.api'})
    assert issues, case


def test_checker_counts_legal_plans_including_readonly_proxy_draft():
    from cgi_pipeline.catalog import list_capabilities
    issues, count = _checker()(ROOT / 'workflows', {spec['api_id'] for spec in list_capabilities()})
    assert issues == []
    assert count == 8  # Seven existing variants plus the read-only Proxy inspection draft.


def test_checker_accepts_null_policy_and_skips_only_explicit_reject(tmp_path, monkeypatch):
    def change(raw, fragment):
        fragment['steps'][0]['failure_policy'] = None
        raw['reject'] = [{'when': {'enabled': True}, 'error': 'intentionally unavailable'}]
        raw['steps_include']['variants'] = [{'when': {'enabled': True},
                                             'overrides': {'one': {'api_id': 'missing.api'}}}]
    directory = _write_flow(tmp_path, monkeypatch, change)
    issues, count = _checker()(directory, {'valid.api'})
    assert issues == []
    assert count == 1
