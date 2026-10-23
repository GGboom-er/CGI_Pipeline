"""Scene tags are persistent; GPU/viewport readiness is an explicit host action."""
from . import discovery


def validate(params):
    if params.get('mode', 'inspect') not in ('inspect', 'apply'):
        raise ValueError('mode must be inspect or apply')
    for key in ('controller_sets', 'controllers', 'exclude'):
        value = params.get(key, [])
        if not isinstance(value, list) or any(not isinstance(n, str) for n in value):
            raise ValueError(key + ' must be an explicit string list')
    for key in ('enable_gpu', 'show_controllers'):
        if not isinstance(params.get(key, True), bool):
            raise ValueError(key + ' must be boolean')


def host_state(cmds):
    opencl_query = getattr(cmds, 'deformerEvaluator', None)
    registered = 'deformer' in (cmds.evaluator(query=True) or [])
    return {'evaluation_mode': (cmds.evaluationManager(query=True, mode=True) or ['off'])[0],
            'gpu_override_enabled': bool(cmds.evaluator(query=True, name='deformer', enable=True)) if registered else False,
            'opencl_valid': bool(opencl_query(query=True, isOpenCLValid=True)) if opencl_query else None,
            'controller_prepopulation': int(cmds.optionVar(query='prepopulateController')),
            'panels': [{'panel': p, 'controllers': cmds.modelEditor(p, query=True, controllers=True),
                        'nurbsCurves': cmds.modelEditor(p, query=True, nurbsCurves=True)}
                       for p in cmds.getPanel(type='modelPanel') or []]}


def restore_host(cmds, state):
    cmds.evaluationManager(mode=state['evaluation_mode'])
    if 'deformer' in (cmds.evaluator(query=True) or []):
        cmds.evaluator(name='deformer', enable=state['gpu_override_enabled'])
    cmds.optionVar(intValue=('prepopulateController', state['controller_prepopulation']))
    for panel in state['panels']:
        cmds.modelEditor(panel['panel'], edit=True, controllers=panel['controllers'], nurbsCurves=panel['nurbsCurves'])


def apply(cmds, params, *, manage_undo=True):
    validate(params)
    data = discovery.inspect(cmds, params)
    if params.get('mode', 'inspect') == 'apply' and params.get('enable_gpu', True):
        if not cmds.pluginInfo('deformerEvaluator', query=True, loaded=True):
            cmds.loadPlugin('deformerEvaluator', quiet=True)
    before = host_state(cmds)
    if params.get('mode', 'inspect') == 'inspect':
        return {**data, 'host': before, 'changed': False}
    if not data['controllers']:
        raise ValueError('No verified controllers; inspect authored sets or pass explicit objects')
    for row in data['controllers']:
        if cmds.referenceQuery(row['path'], isNodeReferenced=True):
            raise ValueError('Controller tagging requires a local editable rig')
    selection = cmds.ls(selection=True, long=True) or []
    chunk, mutated, tagged = 'ControllerTags', False, []
    if manage_undo:
        cmds.undoInfo(openChunk=True, chunkName=chunk)
    try:
        for row in data['controllers']:
            node = row['path']
            if not row['already_tagged']:
                cmds.controller(node); mutated = True
            tags = cmds.listConnections(node + '.message', source=False, destination=True, type='controller') or []
            if len(tags) != 1:
                raise RuntimeError('Expected one controller tag: ' + node)
            tag = tags[0]
            for attr, value in (('prepopulate', True), ('parentprepopulate', True), ('visibilityMode', 0)):
                if cmds.getAttr(tag + '.' + attr) != value:
                    cmds.setAttr(tag + '.' + attr, value); mutated = True
            tagged.append({**row, 'tag': tag})
        if params.get('enable_gpu', True):
            cmds.evaluationManager(mode='parallel')
            cmds.evaluator(name='deformer', enable=True)
            cmds.optionVar(intValue=('prepopulateController', 1))
        if params.get('show_controllers', True):
            for panel in before['panels']:
                cmds.modelEditor(panel['panel'], edit=True, controllers=True, nurbsCurves=True)
        state = host_state(cmds)
        if params.get('enable_gpu', True) and (state['evaluation_mode'] != 'parallel' or not state['gpu_override_enabled']):
            raise RuntimeError('Maya did not accept GPU runtime configuration')
    except Exception:
        if manage_undo:
            cmds.undoInfo(closeChunk=True)
            if mutated and cmds.undoInfo(query=True, undoName=True) == chunk:
                cmds.undo()
        restore_host(cmds, before)
        raise
    else:
        if manage_undo:
            cmds.undoInfo(closeChunk=True)
    finally:
        live = [n for n in selection if cmds.objExists(n)]
        cmds.select(live, replace=True) if live else cmds.select(clear=True)
    return {**data, 'controllers': tagged, 'changed': mutated or state != before, 'host': state,
            'host_before': before, 'tagged_count': len(tagged),
            'new_tag_count': sum(not r['already_tagged'] for r in tagged),
            'persistence': {'scene': 'controller tags and attributes', 'session': 'GPU and viewport settings'},
            'gpu_execution_verified': False}
