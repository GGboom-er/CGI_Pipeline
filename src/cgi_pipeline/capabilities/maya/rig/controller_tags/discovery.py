"""An authored controller set is evidence; a curve or a name alone is not."""


def resolve(cmds, name):
    if not isinstance(name, str) or not name or any(c in name for c in '*?[]'):
        raise ValueError('Controller identities must be explicit whole objects')
    paths = cmds.ls(name, long=True) or []
    if len(paths) != 1:
        raise ValueError('Expected one controller identity: ' + name)
    return paths[0]


def members(cmds, names):
    result, seen = set(), set()
    def visit(name):
        node = resolve(cmds, name)
        if node in seen:
            return
        seen.add(node)
        if cmds.nodeType(node) == 'objectSet':
            for member in cmds.sets(node, query=True) or []:
                visit(member)
        else:
            result.add(node)
    for name in names:
        visit(name)
    return result


def eligible(cmds, node):
    if cmds.nodeType(node) != 'transform':
        return 'not_a_control_transform'
    shapes = cmds.listRelatives(node, shapes=True, noIntermediate=True, fullPath=True) or []
    if not shapes:
        return 'no_control_shape'
    editable = [a for a in cmds.listAttr(node, keyable=True, unlocked=True) or []
                if a not in ('visibility', 'lodVisibility') and cmds.getAttr(node + '.' + a, settable=True)]
    if not editable:
        return 'no_editable_animation_channel'
    # Do not turn a generated deformation/path curve into an animator control.
    for shape in shapes:
        if cmds.nodeType(shape) == 'nurbsCurve':
            incoming = cmds.listConnections(shape + '.create', source=True, destination=False) or []
            if any(cmds.nodeType(n) not in ('makeNurbCircle', 'makeNurbSquare') for n in incoming):
                return 'driven_curve_geometry'
            outgoing = cmds.listConnections(shape + '.worldSpace', source=False, destination=True) or []
            if any(cmds.nodeType(n) in ('wire', 'ikHandle', 'motionPath', 'curveInfo', 'pointOnCurveInfo') for n in outgoing):
                return 'curve_geometry_used_for_rig_computation'
    return ''


def inspect(cmds, params):
    root = resolve(cmds, params.get('rig_root', ''))
    if cmds.nodeType(root) != 'transform':
        raise ValueError('rig_root must be one transform')
    sets = params.get('controller_sets', [])
    explicit = params.get('controllers', [])
    exclude = set(resolve(cmds, n) for n in params.get('exclude', []))
    for name in sets:
        if cmds.nodeType(resolve(cmds, name)) != 'objectSet':
            raise ValueError('controller_sets must contain authored objectSets')
    authored = members(cmds, sets)
    direct = {resolve(cmds, n) for n in explicit}
    existing = set(cmds.ls(cmds.controller(query=True, allControllers=True) or [], long=True) or [])
    owned = lambda n: n == root or n.startswith(root + '|')
    candidates = authored | direct | {n for n in existing if owned(n)}
    if any(not owned(n) for n in authored | direct):
        raise ValueError('Explicit controller set contains objects outside rig_root')
    accepted, skipped = [], []
    for node in sorted(candidates - exclude):
        reason = '' if node in existing else eligible(cmds, node)
        if reason:
            skipped.append({'path': node, 'reason': reason})
        else:
            accepted.append({'path': node, 'uuid': (cmds.ls(node, uuid=True) or [''])[0],
                             'basis': 'existing_tag' if node in existing else 'explicit_object' if node in direct else 'authored_control_set',
                             'already_tagged': node in existing})
    # Bounded evidence for Agent completeness review, never automatically tagged.
    leftovers = set()
    for shape in cmds.ls(root, dag=True, type='nurbsCurve', long=True) or []:
        parent = (cmds.listRelatives(shape, parent=True, fullPath=True) or [''])[0]
        if parent not in candidates and parent not in exclude and not eligible(cmds, parent):
            leftovers.add(parent)
    return {'rig_root': root, 'controllers': accepted, 'skipped': skipped,
            'unclassified_curves': sorted(leftovers), 'controller_sets': sets}
