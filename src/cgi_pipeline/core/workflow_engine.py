# cgi_pipeline/core/workflow_engine.py
# Workflow discovery, expansion and input contracts; execution stays in core.tasks.

import copy
import json
from pathlib import Path

from cgi_pipeline.paths import WORKFLOWS_ROOT
from cgi_pipeline.core.workflow_compiler import compile_workflow
from cgi_pipeline.core.workflow_templates import template_references, step_failure_policy

_WORKFLOWS_DIR = WORKFLOWS_ROOT


def _fragment_path(relative: str) -> Path:
    path = (_WORKFLOWS_DIR / relative).resolve()
    fragments_root = (_WORKFLOWS_DIR / 'fragments').resolve()
    try:
        path.relative_to(fragments_root)
    except ValueError as exc:
        raise ValueError(f'workflow fragment 必须位于 workflows/fragments: {relative}') from exc
    if path.suffix.lower() != '.json' or not path.is_file():
        raise FileNotFoundError(f'workflow fragment 不存在: {relative}')
    return path


def _load_file(path: Path) -> dict:
    from cgi_pipeline.catalog import snapshot
    workflow = json.loads(path.read_text(encoding='utf-8'))
    include = workflow.get('steps_include')
    if include:
        fragment = json.loads(_fragment_path(include['fragment']).read_text(encoding='utf-8'))
        workflow['_fragment_steps'] = fragment.get('steps')
    # Business inputs retain their existing consumers and manifest owner.
    specs, _ = snapshot()
    candidates = workflow.get('_fragment_steps') or workflow.get('steps', [])
    workflow['_business_inputs'] = {}
    if any(step.get('api_id') == 'pipeline.pipeline.resolve_asset_files' for step in candidates):
        workflow['_business_inputs'] = copy.deepcopy(specs['pipeline.pipeline.resolve_asset_files']['inputs'])
        workflow['_business_inputs']['asset_name'] = {'type': 'string', 'required': False}
    if workflow.get('source_resolution'):
        from cgi_pipeline.core.tasks import workflow_source_inputs
        workflow['_business_inputs'].update(workflow_source_inputs())
    return workflow


def list_workflows() -> list[dict]:
    """列出所有已注册的工作流及其步骤概要"""
    results = []
    if not _WORKFLOWS_DIR.exists():
        return results
    for f in sorted(_WORKFLOWS_DIR.glob('*.json')):
        try:
            raw = _load_file(f)
            wf = compile_workflow(raw, {})
            results.append({
                'workflow_id': wf['workflow_id'],
                'name': wf.get('name', ''),
                'status': wf.get('status', 'active'),
                'description': wf.get('description', ''),
                'runtime_lifecycle': wf.get('runtime_lifecycle', 'resident'),
                'steps': [s.get('api_id') or s.get('api_id') for s in wf.get('steps', [])],
                **workflow_input_contract(wf),
            })
        except Exception:
            pass
    return results


def load_workflow(workflow_id: str) -> dict:
    """根据 workflow_id 加载完整工作流定义"""
    for f in _WORKFLOWS_DIR.glob('*.json'):
        raw = json.loads(f.read_text(encoding='utf-8'))
        if raw.get('workflow_id') == workflow_id:
            return _load_file(f)
    raise FileNotFoundError(f'工作流不存在: {workflow_id}')


def _set_binding_example(root, path, value):
    """Build examples with list indices using the same dotted path semantics."""
    current = root
    for index, key in enumerate(path):
        last = index == len(path) - 1
        empty = [] if not last and path[index + 1].isdigit() else {}
        if isinstance(current, list):
            offset = int(key)
            current.extend([None] * max(0, offset + 1 - len(current)))
            if last:
                current[offset] = value
            else:
                if current[offset] is None:
                    current[offset] = empty
                current = current[offset]
        elif last:
            current[key] = value
        else:
            current = current.setdefault(key, empty)


def workflow_input_contract(workflow: dict) -> dict:
    """Derive external bindings from expanded templates and existing API input metadata."""
    from cgi_pipeline.catalog import snapshot
    specs, _ = snapshot()
    workflow = (workflow if 'effective_parameters' in workflow else compile_workflow(workflow, {}))
    inputs = {name: {**copy.deepcopy(definition), 'required': False}
              for name, definition in workflow.get('_business_inputs', {}).items()}
    inputs.update({name: {**copy.deepcopy(definition), 'required': False}
                   for name, definition in workflow.get('parameters', {}).items()})
    runtime_inputs = {'run_dir', 'info_dir', 'source_name'}
    if workflow.get('source_resolution'):
        inputs.setdefault('source_path', {'required': True, 'type': 'string',
                          'description': 'Source file selected for this workflow.',
                          'alternatives': ['source_path', 'project + asset_name via source_resolution',
                                           'workspace_id with an existing source binding']})

    def visit(value, definition=None):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            for ref in template_references(value):
                if ref.kind != 'input':
                    continue
                name = '.'.join(ref.path)
                if ref.path[0] in runtime_inputs:
                    continue
                row = inputs.setdefault(name, {'required': True})
                row['required'] = name not in workflow.get('parameters', {})
                if definition and ref.whole and not ref.filtered:
                    for key in ('type', 'description', 'choices'):
                        if key in definition:
                            row[key] = copy.deepcopy(definition[key])
                if name == 'source_path':
                    row.update(type='string', description='Source file selected for this workflow.')
                    if workflow.get('source_resolution'):
                        row['alternatives'] = ['source_path', 'project + asset_name via source_resolution',
                                               'workspace_id with an existing source binding']

    for step in workflow.get('steps', []):
        for candidate in (step, step_failure_policy(step).get('repair_step', {})):
            definitions = specs.get(candidate.get('api_id'), {}).get('inputs', {})
            for key, value in candidate.get('parameters', {}).items():
                visit(value, definitions.get(key))
            visit(candidate.get('source_path'))
    arguments = {'params': {'workflow_id': workflow.get('workflow_id', ''), 'extra_params': {}}}
    bindings = []
    for name, definition in inputs.items():
        if name == 'source_path':
            path = ['params', 'source_path']
        else:
            path = ['params', 'extra_params', *name.split('.')]
        if 'default' in definition:
            _set_binding_example(arguments, path, copy.deepcopy(definition['default']))
        elif definition.get('required'):
            _set_binding_example(arguments, path, None)
        if definition.get('required'):
            bindings.append({'path': path, **definition})
    return {'input_contract': inputs, 'runtime_inputs': sorted(runtime_inputs),
            **({'source_resolution': copy.deepcopy(workflow['source_resolution'])}
               if workflow.get('source_resolution') else {}),
            'mcp_call_template': {'tool': 'pipeline_execute_workflow', 'arguments': arguments,
                                  'requires_binding': bindings,
                                  'binding_rule': 'Bind each path or one of its declared alternatives; workspace sources must already be recorded.'}}


def validate_workflow_inputs(workflow: dict, payload: dict) -> None:
    """Reject unresolved external inputs before workspace creation or worker startup."""
    from cgi_pipeline.contracts import ApiContractError, validate_params
    from cgi_pipeline.catalog import snapshot
    specs, _ = snapshot()
    for step in workflow.get('steps', []):
        for candidate in (step, step_failure_policy(step).get('repair_step', {})):
            if candidate and candidate.get('api_id') not in specs:
                raise ApiContractError(f'Unknown workflow API: {candidate.get("api_id")}')
            if candidate and specs[candidate['api_id']].get('execution_strategy') == 'control':
                raise ApiContractError('Runtime control APIs are standalone calls; they cannot run inside a scene workflow.')
    values = dict(payload.get('extra_params') or {})
    contract = workflow_input_contract(workflow)['input_contract']
    if 'source_path' in contract:
        source = payload.get('source_path') or values.get('source_path')
        if not source:
            source = _workflow_source_from_existing_owner(workflow, payload, values)
        if source:
            payload['source_path'] = values['source_path'] = source
    for name, definition in contract.items():
        value = values
        for key in name.split('.'):
            if isinstance(value, dict):
                value = value.get(key)
            elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
                value = value[int(key)]
            else:
                value = None
        if value is None or value == '':
            if definition.get('required'):
                raise ApiContractError(f'Missing workflow input: {name}')
            continue
        if 'type' in definition:
            validate_params({'inputs': {name: definition}}, {name: value})


def _workflow_source_from_existing_owner(workflow: dict, payload: dict, values: dict) -> str:
    """Read an existing scene binding or reuse the declared asset resolver; no new state."""
    from cgi_pipeline.catalog import snapshot
    from cgi_pipeline.core.workspace import _resolve, get_scene_binding, WorkspaceError, workspace_project
    project = payload.get('project') or 'default'
    workspace_id = payload.get('workspace_id') or payload.get('business_task_id')
    if workspace_id and payload.get('scene_action', 'continue') == 'continue':
        try:
            run_dir = _resolve(workspace_id, workspace_project(payload), payload.get('business_task_id') or '')
        except WorkspaceError as exc:
            if payload.get('workspace_id') or exc.code != 'WORKSPACE_NOT_FOUND':
                raise
        else:
            specs, _ = snapshot()
            host = next((specs[step['api_id']]['executor'] for step in workflow.get('steps', [])
                         if specs[step['api_id']]['executor'] in ('maya', 'blender')), None)
            binding = get_scene_binding(run_dir, host) if host else {}
            if binding.get('source_path'):
                return binding['source_path']
    if workflow.get('source_resolution'):
        from cgi_pipeline.core.config_loader import load_project_config
        from cgi_pipeline.core.tasks import _resolve_workflow_source_candidate
        from cgi_pipeline.contracts import ApiContractError
        try:
            source, _ = _resolve_workflow_source_candidate(
                workflow, load_project_config(project), payload.get('asset_name') or '', values)
        except FileNotFoundError as exc:
            raise ApiContractError(str(exc)) from exc
        return source
    return ''
