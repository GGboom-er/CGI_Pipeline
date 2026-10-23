"""Pure compilation of loaded workflow data into one JSON-safe execution plan."""
import copy
import itertools
import json

from cgi_pipeline.contracts import ApiContractError
from cgi_pipeline.contract_values import typed_choice_matches
from cgi_pipeline.core.workflow_templates import (
    template_references, validate_step_templates, step_failure_policy,
)


class WorkflowRejectedError(ApiContractError):
    """A declared control combination is intentionally unavailable."""


def _merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = (_merge(result[key], value)
                       if isinstance(value, dict) and isinstance(result.get(key), dict)
                       else copy.deepcopy(value))
    return result


def _matches(when, controls):
    if not isinstance(when, dict) or not when:
        raise ValueError('when must be a nonempty literal equality object')
    for key, value in when.items():
        if key not in controls or type(value) is not type(controls[key]):
            raise ValueError(f'when has unknown or incorrectly typed control: {key}')
    return all(controls[key] == value for key, value in when.items())


def _controls(raw, extra):
    reserved = set(extra) & {'run_dir', 'info_dir', 'source_name'}
    if reserved:
        raise ApiContractError(f'Workflow runtime input(s) cannot be overridden: {", ".join(sorted(reserved))}')
    controls = {}
    for name, spec in raw.get('parameters', {}).items():
        value = extra.get(name, spec.get('default'))
        expected = {'boolean': bool, 'string': str}.get(spec.get('type'))
        if expected is None or type(value) is not expected:
            raise ApiContractError(f'Workflow parameter {name} must be {spec.get("type")}')
        if 'enum' in spec and not typed_choice_matches(value, spec['enum']):
            raise ApiContractError(f'Workflow parameter {name} must be one of {spec["enum"]}')
        controls[name] = value
    template_names = {ref.path[0] for ref in template_references(raw) if ref.kind == 'input'}
    allowed = set(controls) | set(raw.get('_business_inputs', {})) | template_names
    unknown = set(extra) - allowed
    if unknown:
        raise ApiContractError(f'Unknown workflow parameter(s): {", ".join(sorted(unknown))}')
    for rule in raw.get('reject', []):
        if _matches(rule.get('when'), controls):
            raise WorkflowRejectedError(rule['error'])
    return controls


def _validate_failure_policy(step):
    policy = step_failure_policy(step)
    action = policy.get('action', 'abort')
    if action not in {'abort', 'warn_continue', 'repair_then_retry', 'needs_attention'}:
        raise ValueError(f'invalid failure_policy.action: {action}')
    if action != 'repair_then_retry':
        return
    repair = policy.get('repair_step')
    if not isinstance(repair, dict) or not repair.get('api_id'):
        raise ValueError('repair_then_retry requires repair_step.api_id')
    if type(policy.get('max_attempts', 1)) is not int or policy.get('max_attempts', 1) != 1:
        raise ValueError('repair_then_retry only allows max_attempts=1')
    if policy.get('on_exhausted', 'needs_attention') not in {'abort', 'needs_attention'}:
        raise ValueError('repair_then_retry.on_exhausted must be abort or needs_attention')


def _expand(raw, controls):
    include = copy.deepcopy(raw.get('steps_include'))
    if include is None:
        return copy.deepcopy(raw.get('steps', []))
    if raw.get('steps'):
        raise ValueError('workflow cannot declare both steps and steps_include')
    if not isinstance(include, dict) or not include.get('fragment'):
        raise ValueError('steps_include.fragment is required')
    for variant in include.pop('variants', []):
        if _matches(variant.get('when'), controls):
            include = _merge(include, {key: value for key, value in variant.items() if key != 'when'})
    source = raw.get('_fragment_steps')
    if not isinstance(source, list):
        raise ValueError('loaded workflow fragment.steps must be an array')
    by_id = {step.get('step_id'): step for step in source}
    if None in by_id or len(by_id) != len(source):
        raise ValueError('fragment step_id must be unique and nonempty')
    selected = include.get('select', list(by_id))
    if not isinstance(selected, list) or any(name not in by_id for name in selected):
        raise ValueError('fragment select contains an unknown step_id')
    overrides = include.get('overrides', {})
    if not isinstance(overrides, dict) or any(name not in selected for name in overrides):
        raise ValueError('fragment overrides must refer to selected steps')
    insertions = include.get('insert_after', {})
    if not isinstance(insertions, dict) or any(name not in selected for name in insertions):
        raise ValueError('fragment insert_after must refer to selected steps')
    steps = []
    for name in selected:
        steps.append(_merge(by_id[name], overrides.get(name, {})))
        additions = insertions.get(name, [])
        if not isinstance(additions, list):
            raise ValueError(f'insert_after.{name} must be an array')
        steps.extend(copy.deepcopy(additions))
    return steps


def compile_workflow(raw, extra_params=None):
    """Validate controls, select composition and freeze all executable workflow data."""
    if raw.get('runtime_lifecycle', 'resident') not in ('resident', 'temporary'):
        raise ApiContractError('Workflow runtime_lifecycle must be resident or temporary')
    if extra_params is None:
        extra_params = {}
    if not isinstance(extra_params, dict):
        raise ApiContractError('Workflow extra_params must be an object')
    controls = _controls(raw, extra_params)
    expanded = _expand(raw, controls)
    steps = [step for step in expanded if 'when' not in step or _matches(step['when'], controls)]
    ids = [step.get('step_id') for step in steps]
    if any(not isinstance(name, str) or not name or name == '__failed__' for name in ids) or len(ids) != len(set(ids)):
        raise ValueError('expanded workflow step_id must be unique, nonempty and not reserved')
    for step in steps:
        step.pop('when', None)
        if not step.get('api_id'):
            raise ValueError(f'step {step["step_id"]} requires api_id')
        _validate_failure_policy(step)
    validate_step_templates(steps)
    plan = {key: copy.deepcopy(value) for key, value in raw.items()
            if key not in {'steps_include', '_fragment_steps', 'steps', 'reject'}}
    plan.update(steps=steps, effective_parameters=controls,
                extra_params={**copy.deepcopy(extra_params), **controls},
                save_requested=any(step['api_id'] == 'maya.asset.save_scene' for step in steps))
    # Explicit editing variants retain their live scene; automated variants
    # use the declared temporary lifecycle. Reuse the same literal matcher.
    if any(_matches(when, controls) for when in raw.get('resident_when', [])):
        plan['runtime_lifecycle'] = 'resident'
    return json.loads(json.dumps(plan, allow_nan=False))


def iter_workflow_variants(raw):
    """Compile every finite control combination using the execution compiler.

    Explicit reject rules alone are skipped. A malformed branch propagates its
    error, and an unbounded branch domain or zero legal plans fails the check.
    """
    branching = set()

    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get('when'), dict):
                branching.update(value['when'])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(raw)
    names, domains = [], []
    for name, spec in raw.get('parameters', {}).items():
        domain = spec.get('enum')
        if domain is None:
            if spec.get('type') == 'boolean':
                domain = [False, True]
            elif name in branching:
                raise ValueError(f'Cannot enumerate workflow branch control: {name}')
            else:
                domain = [spec.get('default')]
        if not isinstance(domain, list) or not domain:
            raise ValueError(f'Cannot enumerate empty workflow control: {name}')
        names.append(name)
        domains.append(domain)
    emitted = False
    for values in itertools.product(*domains):
        controls = dict(zip(names, values))
        try:
            compiled = compile_workflow(raw, controls)
        except WorkflowRejectedError:
            continue
        emitted = True
        yield controls, compiled
    if not emitted:
        raise ValueError('Workflow has no legal enumerable control combination')
