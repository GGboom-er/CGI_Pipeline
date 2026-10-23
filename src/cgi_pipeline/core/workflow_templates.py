"""Pure workflow template grammar shared by compilation, discovery and execution.

Only an explicitly named, not-yet-produced segment output may be deferred. A
missing input/config/key is an error, never a literal parameter sent to a DCC.
"""
import ast
import copy
import os
import re
from dataclasses import dataclass

from cgi_pipeline.contracts import ApiContractError


class TemplateError(ApiContractError):
    code = 'WORKFLOW_TEMPLATE_INVALID'


@dataclass(frozen=True)
class Reference:
    kind: str
    path: tuple[str, ...]
    whole: bool
    filtered: bool = False


def _split_filters(expression):
    parts, start, quote, escaped, depth = [], 0, '', False, 0
    for index, char in enumerate(expression):
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = ''
        elif char in "\"'":
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                raise TemplateError(f'Invalid template expression: {expression}')
        elif char == '|' and not depth:
            parts.append(expression[start:index].strip())
            start = index + 1
    if quote or depth:
        raise TemplateError(f'Invalid template expression: {expression}')
    return [*parts, expression[start:].strip()]


def _parse(expression):
    base, *tokens = _split_filters(expression)
    match = re.fullmatch(r'(outputs|input|config)\.([\w-]+(?:\.[\w-]+)*)', base)
    if not match:
        raise TemplateError(f'Invalid template expression: {expression}')
    kind, path = match[1], tuple(match[2].split('.'))
    if kind == 'outputs' and len(path) < 2:
        raise TemplateError(f'Template output reference requires a field: {expression}')
    filters = []
    for token in tokens:
        if token in ('basename', 'stem', 'dirname'):
            filters.append((token, ()))
            continue
        try:
            node = ast.parse(token, mode='eval').body
            if (not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name)
                    or node.func.id != 'replace' or node.keywords or len(node.args) != 2):
                raise ValueError
            args = tuple(ast.literal_eval(arg) for arg in node.args)
            if any(not isinstance(arg, str) for arg in args):
                raise ValueError
        except (SyntaxError, ValueError, TypeError) as exc:
            raise TemplateError(f'Invalid template filter: {token}') from exc
        filters.append(('replace', args))
    return kind, path, filters


def _placeholders(text):
    cursor = 0
    while cursor < len(text):
        start = text.find('{{', cursor)
        closing = text.find('}}', cursor)
        if closing >= 0 and (start < 0 or closing < start):
            raise TemplateError(f'Unmatched template closing delimiter: {text}')
        if start < 0:
            return
        index, quote, escaped = start + 2, '', False
        while index < len(text):
            char = text[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == quote:
                    quote = ''
            elif char in "\"'":
                quote = char
            elif text.startswith('}}', index):
                end = index + 2
                yield start, end, _parse(text[start + 2:index].strip())
                cursor = end
                break
            elif char in '{}':
                raise TemplateError(f'Invalid template delimiter: {text}')
            index += 1
        else:
            raise TemplateError(f'Unclosed template placeholder: {text}')


def template_references(value):
    """Yield parsed references recursively, raising on malformed template syntax."""
    if isinstance(value, dict):
        for child in value.values():
            yield from template_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from template_references(child)
    elif isinstance(value, str):
        for start, end, (kind, path, filters) in _placeholders(value):
            yield Reference(kind, path, not value[:start].strip() and not value[end:].strip(), bool(filters))


def step_failure_policy(step):
    """Normalize absent/null policies; reject every other non-object value."""
    policy = step.get('failure_policy')
    if policy is None:
        return {}
    if not isinstance(policy, dict):
        raise TemplateError(f'step {step.get("step_id")} failure_policy must be an object')
    return policy


def validate_step_templates(steps, previous_output_ids=()):
    """Apply the same recursive grammar and output ordering to all step fields."""
    appeared = set(previous_output_ids)
    for index, step in enumerate(steps):
        repair = step_failure_policy(step).get('repair_step', {})
        candidates = [(step.get('parameters', {}), False), (step.get('source_path'), False),
                      (repair.get('parameters', {}), True), (repair.get('source_path'), False)]
        for value, repairing in candidates:
            references = {ref.path[0] for ref in template_references(value) if ref.kind == 'outputs'}
            unavailable = references - appeared - ({'__failed__'} if repairing else set())
            if unavailable:
                error = TemplateError(f'step {step.get("step_id", index)} has unavailable '
                                      f'template output reference(s): {sorted(unavailable)}')
                error.step_index = index
                raise error
        appeared.add(step.get('step_id') or step.get('api_id'))


def _lookup(root, path, expression):
    current = root
    for key in path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            raise TemplateError(f'Missing template reference: {expression}')
    return current


def _filter(value, filters):
    for name, args in filters:
        if name == 'replace':
            value = str(value).replace(*args)
        else:
            normalized = str(value).replace('\\', '/').rstrip('/')
            basename = normalized.rsplit('/', 1)[-1]
            if name == 'basename':
                value = basename
            elif name == 'stem':
                value = os.path.splitext(basename)[0]
            else:
                value = normalized.rsplit('/', 1)[0] if '/' in normalized else ''
    return value


def resolve_templates(value, outputs=None, extra_params=None, config=None, *, deferred_output_ids=()):
    """Resolve dict/list values and strings while preserving full-value types."""
    outputs, extra_params, config = outputs or {}, extra_params or {}, config or {}
    deferred = set(deferred_output_ids)

    def resolve_string(text):
        placeholders = list(_placeholders(text))
        pieces, cursor = [], 0
        for start, end, (kind, path, filters) in placeholders:
            reference = kind + '.' + '.'.join(path)
            if kind == 'outputs' and path[0] not in outputs and path[0] in deferred:
                resolved = text[start:end]
            else:
                root = {'outputs': outputs, 'input': extra_params, 'config': config}[kind]
                # Historical output ports may contain dots; exact port wins.
                port = '.'.join(path[1:])
                step = outputs.get(path[0]) if kind == 'outputs' else None
                resolved = (step[port] if isinstance(step, dict) and port in step
                            else _lookup(root, path, reference))
                resolved = _filter(resolved, filters)
            if len(placeholders) == 1 and not text[:start].strip() and not text[end:].strip():
                return copy.deepcopy(resolved)
            pieces.extend((text[cursor:start], str(resolved)))
            cursor = end
        return ''.join([*pieces, text[cursor:]])

    def visit(child):
        if isinstance(child, dict):
            return {key: visit(item) for key, item in child.items()}
        if isinstance(child, list):
            return [visit(item) for item in child]
        return resolve_string(child) if isinstance(child, str) else child

    return visit(value)
