"""One grammar must validate and execute real workflow parameter shapes."""
import importlib

import pytest

from cgi_pipeline.core import tasks, workflow_engine as engine


def resolve(value, outputs=None, inputs=None, config=None, **kwargs):
    return tasks._resolve_template_vars(value, outputs or {}, inputs or {}, config, **kwargs)


def test_nested_nodes_rename_keeps_types_and_hyphen_step_ids():
    params = {'items': [{'node': '{{outputs.find-node.items.0.path}}',
                         'name': '{{input.names.0}}'}], 'force': '{{input.force}}',
              'metadata': '{{outputs.find-node.metadata}}', 'zero': '{{input.zero}}'}
    raw = {'steps': [{'step_id': 'find-node', 'api_id': 'maya.scene.nodes.find'},
                     {'step_id': 'rename', 'api_id': 'maya.scene.nodes.rename', 'parameters': params}]}
    engine.compile_workflow(raw)
    actual = resolve(params, {'find-node': {'items': [{'path': '|root|old'}], 'metadata': {'ok': True}}},
                     {'names': ['new'], 'force': False, 'zero': 0})
    assert actual == {'items': [{'node': '|root|old', 'name': 'new'}], 'force': False,
                      'metadata': {'ok': True}, 'zero': 0}


def test_filters_keep_unicode_quoted_pipes_escapes_and_mixed_strings():
    value = {'path': '{{input.path | dirname}}', 'name': '{{input.path | basename}}',
             'stem': '{{input.path | stem}}',
             'text': "prefix {{input.word | replace('中|文', '汉字') | replace('字', '语')}} {{input.zero}}"}
    assert resolve(value, inputs={'path': r'Y:\目录\a.blend', 'word': '中|文', 'zero': 0}) == {
        'path': 'Y:/目录', 'name': 'a.blend', 'stem': 'a', 'text': 'prefix 汉语 0'}
    assert resolve({'literal': '{{input.word | replace("a", "b}}c")}}'}, inputs={'word': 'a'}) == {'literal': 'b}}c'}


def test_dotted_output_key_priority_and_config_list_index():
    assert resolve({'flat': '{{outputs.find.result.path}}', 'config': '{{config.roots.0.path}}'},
                   {'find': {'result.path': 'flat', 'result': {'path': 'nested'}}},
                   config={'roots': [{'path': 'root'}]}) == {'flat': 'flat', 'config': 'root'}


@pytest.mark.parametrize('value', ['{{input.missing}}', '{{outputs.read.missing}}',
                                  '{{config.paths.4}}', '{{input.x | unknown}}',
                                  '{{input.x | replace("a")}}', '{{input.x', '{{bad.x}}'])
def test_runtime_rejects_missing_or_invalid_templates(value):
    with pytest.raises(ValueError, match='template|Template'):
        resolve({'nested': [value]}, {'read': {}}, {'x': 'a'})


@pytest.mark.parametrize('value', ['{{input.x | unknown}}', '{{input.x | replace("a")}}',
                                  '{{input.x', '{{bad.x}}', '{{outputs.read}}'])
def test_compile_rejects_invalid_grammar_before_execution(value):
    with pytest.raises(ValueError, match='template|Template'):
        engine.compile_workflow({'steps': [{'step_id': 'read', 'api_id': 'maya.scene.selection.get',
                                           'parameters': {'nested': [value]}}]})


def test_explicit_deferred_outputs_are_narrow_and_strict_when_present():
    value = {'nested': ['{{outputs.find-node.items.0.path}}', '{{input.value}}']}
    assert resolve(value, inputs={'value': False}, deferred_output_ids={'find-node'}) == {
        'nested': ['{{outputs.find-node.items.0.path}}', False]}
    with pytest.raises(ValueError, match='find-node'):
        resolve(value, inputs={'value': False})
    with pytest.raises(ValueError, match='missing'):
        resolve({'x': '{{outputs.find-node.missing}}'}, {'find-node': {}},
                deferred_output_ids={'find-node'})
    with pytest.raises(ValueError, match='input'):
        resolve(value, deferred_output_ids={'find-node'})


def test_input_contract_uses_same_hyphen_path_and_full_placeholder_definition():
    raw = {'steps': [{'step_id': 'read', 'api_id': 'maya.scene.selection.set',
                     'parameters': {'nodes': '{{input.node-list}}'}}]}
    contract = engine.workflow_input_contract(raw)['input_contract']
    assert contract['node-list']['type'] == 'array'
    assert contract['node-list']['required'] is True


def test_control_variant_enumerator_skips_only_explicit_rejects():
    compiler = importlib.import_module('cgi_pipeline.core.workflow_compiler')
    variants = list(compiler.iter_workflow_variants(engine.load_workflow('tex_to_rig_verify_and_sync')))
    assert sorted(len(plan['steps']) for _, plan in variants) == [5, 9, 10, 11, 12]
    raw = {'parameters': {'mode': {'type': 'string', 'default': 'a'}}, 'steps': [
        {'step_id': 'read', 'api_id': 'maya.scene.selection.get', 'when': {'mode': 'a'}}]}
    with pytest.raises(ValueError, match='enumerat'):
        list(compiler.iter_workflow_variants(raw))
    raw['parameters']['mode']['enum'] = ['a', 'b']
    raw['steps'][0]['parameters'] = {'bad': '{{input.x | unknown}}'}
    with pytest.raises(ValueError, match='template|Template'):
        list(compiler.iter_workflow_variants(raw))


def test_repair_failed_output_is_never_allowed_as_source_path():
    raw = {'steps': [{'step_id': 'read', 'api_id': 'maya.scene.selection.get',
                     'failure_policy': {'action': 'repair_then_retry', 'repair_step': {
                         'api_id': 'maya.scene.selection.get',
                         'parameters': {'x': '{{outputs.__failed__.result}}'},
                         'source_path': '{{outputs.__failed__.result}}'}}}]}
    with pytest.raises(ValueError, match='reference'):
        engine.compile_workflow(raw)


def test_control_enumerator_does_not_hide_bad_variant_or_zero_legal_plans():
    compiler = importlib.import_module('cgi_pipeline.core.workflow_compiler')
    raw = {'parameters': {'enabled': {'type': 'boolean', 'default': False}}, 'steps': [
        {'step_id': 'first', 'api_id': 'maya.scene.selection.get'},
        {'step_id': 'second', 'api_id': 'maya.scene.selection.get', 'when': {'enabled': True},
         'parameters': {'x': '{{outputs.later.result}}'}}]}
    with pytest.raises(ValueError, match='reference'):
        list(compiler.iter_workflow_variants(raw))
    raw['steps'].pop()
    raw['reject'] = [{'when': {'enabled': value}, 'error': 'unavailable'} for value in (False, True)]
    with pytest.raises(ValueError, match='no legal'):
        list(compiler.iter_workflow_variants(raw))


def test_control_enum_does_not_accept_integer_as_boolean_alias():
    raw = {'parameters': {'enabled': {'type': 'boolean', 'default': False, 'enum': [0]}}, 'steps': []}
    with pytest.raises(ValueError, match='one of'):
        engine.compile_workflow(raw)


def test_external_numeric_list_index_uses_same_lookup_as_execution():
    raw = {'steps': [{'step_id': 'rename', 'api_id': 'maya.scene.nodes.rename', 'parameters': {
        'items': [{'node': '{{input.names.0}}', 'name': 'new'}]}}]}
    workflow = engine.compile_workflow(raw, {'names': ['old']})
    example = engine.workflow_input_contract(workflow)['mcp_call_template']['arguments']
    assert example['params']['extra_params']['names'] == [None]
    engine.validate_workflow_inputs(workflow, {'extra_params': {'names': ['old']}})
    with pytest.raises(ValueError, match='Missing workflow input'):
        engine.validate_workflow_inputs(workflow, {'extra_params': {'names': []}})
