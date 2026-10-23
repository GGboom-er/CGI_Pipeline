"""Compile -> prepared segment -> real task dispatch uses one strict resolver."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from cgi_pipeline.contracts import make_api_receipt
from cgi_pipeline.core import tasks, workflow_engine as engine
from cgi_pipeline.server import internals
from cgi_pipeline.server.models import ExecuteWorkflowInput
from test_workspace import root
from test_workspace_execution import runtime
from test_workflow_composition_and_failure import _receipt, _run_chain


def install_workflow(runtime, monkeypatch, steps):
    monkeypatch.setattr(engine, 'load_workflow', lambda _: {'workflow_id': 'template-fixture', 'steps': steps})

    def run_api(payload):
        runtime.calls.append(payload)
        output = {'items': [{'path': '|root|old'}], 'result': {'target': '|root|old'}}
        return make_api_receipt(payload['api_id'], '1.0', 'SUCCESS', time.perf_counter(), output=output)

    runtime.worker.run_api = run_api


def execute(runtime):
    return asyncio.run(runtime.mcp.pipeline_execute_workflow(ExecuteWorkflowInput(
        workflow_id='template-fixture', source_path=str(runtime.source))))


def test_same_segment_nested_rename_resolves_predecessor_at_dispatch(runtime, monkeypatch):
    install_workflow(runtime, monkeypatch, [
        {'step_id': 'find-node', 'api_id': 'maya.scene.nodes.find'},
        {'step_id': 'rename', 'api_id': 'maya.scene.nodes.rename', 'parameters': {
            'items': [{'node': '{{outputs.find-node.items.0.path}}', 'name': 'new'}]}}])
    result = execute(runtime)
    assert result['status'] == 'WORKFLOW_SUCCESS', result
    assert runtime.calls[1]['api_params']['items'] == [{'node': '|root|old', 'name': 'new'}]
    assert len(runtime.opened) == 1


@pytest.mark.parametrize('field', ['parameters', 'source_path'])
def test_missing_produced_field_is_terminal_before_consumer_and_keeps_receipts(runtime, monkeypatch, field):
    consumer = {'step_id': 'rename', 'api_id': 'maya.scene.nodes.rename', 'parameters': {}}
    if field == 'parameters':
        consumer[field] = {'items': [{'node': '{{outputs.find-node.missing}}', 'name': 'new'}]}
    else:
        consumer[field] = '{{outputs.find-node.missing}}'
    install_workflow(runtime, monkeypatch, [{'step_id': 'find-node', 'api_id': 'maya.scene.nodes.find'}, consumer])
    result = execute(runtime)
    assert result['status'] == 'WORKFLOW_ABORTED', result
    assert len(runtime.calls) == 1
    assert result['error_code'] == 'WORKFLOW_TEMPLATE_INVALID'
    audit = (runtime.root / (result['task_id'] + '.json')).read_text(encoding='utf-8')
    assert 'CHAIN_ERROR' in audit and 'Missing template reference' in audit
    assert 'RETRY' not in audit
    recovered = internals.get_task_result(result['task_id'])
    assert recovered['status'] == 'WORKFLOW_ABORTED'
    assert len(recovered['step_receipts']) == 1


@pytest.mark.parametrize('value', ['{{input.x | unknown}}', '{{outputs.later.result}}', '{{input.x'])
def test_invalid_new_workflow_does_not_bind_or_dispatch(runtime, monkeypatch, value):
    install_workflow(runtime, monkeypatch, [
        {'step_id': 'first', 'api_id': 'maya.scene.nodes.rename', 'parameters': {'items': [value]}},
        {'step_id': 'later', 'api_id': 'maya.scene.nodes.find'}])
    result = execute(runtime)
    assert result['error_code'] == 'WORKFLOW_INPUT_INVALID', result
    assert runtime.calls == [] and runtime.pending == [] and runtime.opened == []
    assert not (runtime.root / 'projects').exists()


def test_missing_repair_reference_is_terminal_and_does_not_replay_failed_api(tmp_path):
    api_id = 'pipeline.pipeline.resolve_asset_files'
    step = {'step_id': 'check', 'api_id': api_id, 'parameters': {}, 'failure_policy': {
        'action': 'repair_then_retry', 'repair_step': {'api_id': api_id,
            'parameters': {'nested': [{'result': '{{outputs.__failed__.missing}}'}]}}}}
    result, calls = _run_chain(tmp_path, [step], [_receipt(api_id, 'AUDIT_FAILED', {'result': {'issue': 'minor'}})])
    assert result['status'] == 'CHAIN_ERROR', result
    assert result['error_code'] == 'WORKFLOW_TEMPLATE_INVALID'
    assert result['failed_step'] == 0 and len(calls) == 1
    assert len(result['chain_results']) == 1


def test_standalone_invalid_chain_never_dispatches(tmp_path):
    step = {'step_id': 'check', 'api_id': 'pipeline.pipeline.resolve_asset_files',
            'parameters': {'nested': ['{{outputs.later.result}}']}}
    result, calls = _run_chain(tmp_path, [step], [])
    assert result['status'] == 'CHAIN_ERROR', result
    assert result['error_code'] == 'WORKFLOW_TEMPLATE_INVALID'
    assert result['failed_step'] == 0 and calls == []


def test_cancel_before_deferred_consumer_keeps_first_receipt(runtime, monkeypatch):
    install_workflow(runtime, monkeypatch, [
        {'step_id': 'find-node', 'api_id': 'maya.scene.nodes.find'},
        {'step_id': 'rename', 'api_id': 'maya.scene.nodes.rename', 'parameters': {
            'items': [{'node': '{{outputs.find-node.items.0.path}}', 'name': 'new'}]}}])
    original = runtime.worker.run_api

    def cancel(payload):
        result = original(payload)
        task_id = payload['task_id'].split('_seg')[0]
        with (runtime.root / (task_id + '.json')).open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'task_id': task_id, 'status': 'CANCEL_REQUESTED'}) + '\n')
        return result

    runtime.worker.run_api = cancel
    result = execute(runtime)
    assert result['status'] == 'CANCELLED', result
    assert len(runtime.calls) == 1 and len(result['step_receipts']) == 1


def test_null_failure_policy_compiles_exposes_inputs_and_defaults_to_abort(tmp_path):
    api_id = 'pipeline.pipeline.resolve_asset_files'
    raw = {'steps': [
        {'step_id': 'first', 'api_id': api_id, 'parameters': {'asset_name': '{{input.asset}}'},
         'failure_policy': None},
        {'step_id': 'second', 'api_id': api_id, 'failure_policy': None}]}
    compiled = engine.compile_workflow(raw, {'asset': 'fixture'})
    assert engine.workflow_input_contract(compiled)['input_contract']['asset']['required']
    engine.validate_workflow_inputs(compiled, {'extra_params': {'asset': 'fixture'}})
    # The real chain also receives an explicit null policy, independently of
    # workflow preparation dropping absent policies when constructing segments.
    compiled['steps'][0]['parameters'] = {'asset_name': 'fixture'}
    result, calls = _run_chain(tmp_path, compiled['steps'], [_receipt(api_id, 'ERROR')])
    assert result['status'] == 'CHAIN_ABORTED', result
    assert len(calls) == 1 and len(result['chain_results']) == 1


@pytest.mark.parametrize('policy', [False, 0, [], 'abort'])
def test_non_object_failure_policy_stays_invalid_in_all_consumers(tmp_path, policy):
    step = {'step_id': 'first', 'api_id': 'pipeline.pipeline.resolve_asset_files',
            'failure_policy': policy}
    with pytest.raises(ValueError, match='failure_policy must be an object'):
        engine.compile_workflow({'steps': [step]})
    precompiled = {'effective_parameters': {}, 'steps': [step]}
    with pytest.raises(ValueError, match='failure_policy must be an object'):
        engine.workflow_input_contract(precompiled)
    with pytest.raises(ValueError, match='failure_policy must be an object'):
        engine.validate_workflow_inputs(precompiled, {})
    result, calls = _run_chain(tmp_path, [step], [])
    assert result['status'] == 'CHAIN_ERROR', result
    assert result['error_code'] == 'WORKFLOW_TEMPLATE_INVALID'
    assert calls == []


@pytest.mark.parametrize('templated_source', [True, False])
def test_batch_source_templates_fall_back_but_literal_batch_remains_native(tmp_path, monkeypatch, templated_source):
    calls, batches = [], []
    api_id = 'pipeline.pipeline.resolve_asset_files'

    def run_api(payload):
        calls.append(payload)
        return {'status': 'SUCCESS', 'detail': json.dumps(_receipt(api_id, 'SUCCESS', {'result': {}}))}

    def run_batch(payloads, atomic):
        batches.append(payloads)
        results = [run_api(payload) for payload in payloads]
        return {'status': 'SUCCESS', 'results': results, 'requested': len(payloads),
                'completed': len(results), 'atomic': atomic, 'rolled_back': False}

    monkeypatch.setattr(tasks, '_create_worker', lambda *args: SimpleNamespace(run_api=run_api, run_batch=run_batch))
    monkeypatch.setattr(tasks, '_get_audit_path', lambda *args: tmp_path / 'audit.jsonl')
    monkeypatch.setattr(tasks, '_update_progress', lambda *args: None)
    monkeypatch.setattr(tasks, 'publish_event', lambda *args: None)
    result = tasks.execute_api_chain.run({
        'task_id': 'template-batch', 'project': 'default', 'run_dir': str(tmp_path),
        'suppress_report': True, 'batch_mode': True, 'atomic': True, 'api_chain': [
            {'step_id': 'first', 'api_id': api_id, 'parameters': {}},
            {'step_id': 'second', 'api_id': api_id, 'parameters': {},
             'source_path': '{{outputs.first.missing}}' if templated_source else 'literal.ma'}]})
    if templated_source:
        assert result['status'] == 'CHAIN_ERROR', result
        assert result['error_code'] == 'WORKFLOW_TEMPLATE_INVALID'
        assert result['failed_step'] == 1 and len(result['chain_results']) == 1
        assert len(calls) == 1 and batches == []
    else:
        assert result['status'] == 'SUCCESS', result
        assert len(calls) == 2 and len(batches) == 1
        assert len(result['chain_results']) == 2
