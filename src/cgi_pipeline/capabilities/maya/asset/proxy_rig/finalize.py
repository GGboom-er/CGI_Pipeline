"""Finalize one recorded candidate; delete backups, preserve live rig inputs."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from time import perf_counter

from . import deletion_scope, outside, safety


def _resolved(cmds, rows, key='uuid'):
    return {r[key]: safety.resolve(cmds, r[key]) for r in rows}


def _geometry(cmds, paths):
    import hashlib
    from maya.api import OpenMaya as om
    result = {}
    for path in paths:
        shapes = cmds.listRelatives(path, shapes=True, noIntermediate=True, fullPath=True, type='mesh') or []
        for shape in shapes:
            selection = om.MSelectionList(); selection.add(shape)
            fn = om.MFnMesh(selection.getDagPath(0))
            points = [(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)]
            result[safety.identity(cmds, shape)] = (fn.numPolygons, hashlib.sha256(repr(points).encode()).hexdigest())
    return result


def _history_consumers(cmds, node, shapes, history):
    """Deleting a mesh can cascade into upstream deformers and combo drivers."""
    allowed = set(history) | set(shapes) | {node}
    allowed.update(n.rsplit('|', 1)[-1] for n in allowed.copy())
    problems = []
    for source in history:
        inherited = cmds.nodeType(source, inherited=True) or []
        if 'geometryFilter' not in inherited and cmds.nodeType(source) != 'combinationShape':
            continue
        for target in cmds.listConnections(source, source=False, destination=True) or []:
            if target in allowed or cmds.nodeType(target) in ('objectSet', 'shadingEngine', 'nodeGraphEditorInfo', 'shapeEditorManager'):
                continue
            problems.append(source + ' -> ' + target)
    return problems


def _connections(cmds, paths):
    """Guard native dynamic inputs, not merely the current evaluated points."""
    nodes = set()
    for path in paths:
        for node in cmds.listHistory(path, pruneDagObjects=True) or []:
            if 'geometryFilter' in (cmds.nodeType(node, inherited=True) or []) or cmds.nodeType(node) == 'combinationShape':
                nodes.add(node)
    identities = {}
    def plug_identity(plug):
        node, attr = plug.split('.', 1)
        if node not in identities:
            identities[node] = safety.identity(cmds, node)
        return identities[node] + '.' + attr
    result = {}
    for node in nodes:
        pairs = cmds.listConnections(node, source=True, destination=False, plugs=True, connections=True) or []
        result[safety.identity(cmds, node)] = sorted((plug_identity(pairs[i]), plug_identity(pairs[i+1]))
                                                    for i in range(0, len(pairs), 2))
    return result


def inspect(cmds, build):
    """Exact manifest identities, not a wildcard or name-only deletion policy."""
    scene = cmds.file(query=True, sceneName=True) or ''
    if Path(scene).resolve() != Path(build['output_path']).resolve():
        raise ValueError('Finalize requires the actual recorded candidate scene')
    safety.managed(scene, suffix={'.ma', '.mb'})
    records = build.get('retained_sources', [])
    paths = _resolved(cmds, records)
    outputs = _resolved(cmds, build['outputs'], 'result_uuid')
    outputs.update(_resolved(cmds, build['kept'] + build['protected']))
    if set(paths) & set(outputs):
        raise ValueError('Candidate sources overlap final outputs')
    scripts = outside.script_sources(cmds)
    groups, deletable, inputs = {}, [], []
    for row in records:
        node = paths[row['uuid']]
        if node != row['path']:
            raise ValueError('Candidate original moved since build: ' + node)
        parent = (cmds.listRelatives(node, parent=True, fullPath=True) or [''])[0]
        if parent.rsplit('|', 1)[-1].split(':')[-1] != 'cgi_proxy_originals':
            raise ValueError('Source is outside its recorded backup container: ' + node)
        groups.setdefault(parent, set()).add(node)
        if cmds.referenceQuery(node, isNodeReferenced=True) or cmds.listRelatives(node, children=True, type='transform'):
            raise ValueError('Backup is referenced or has child transforms: ' + node)
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True, type='mesh') or []
        history = cmds.listHistory(node, pruneDagObjects=True) or []
        scope = deletion_scope.inspect(cmds, node, shapes, history)
        allowed = set(history) | set(shapes)
        consumers = [n for n in cmds.listConnections(node, source=False, destination=True) or []
                     if n not in allowed and cmds.nodeType(n) not in ('objectSet', 'shadingEngine', 'displayLayer')]
        reasons = scope['external_consumers'] + consumers + _history_consumers(cmds, node, shapes, history)
        reasons += [n + ':name_dependency' for n in outside.name_users([node] + shapes, scripts)]
        if reasons:
            inputs.append({**row, 'consumers': sorted(set(reasons))})
        else:
            deletable.append(row)
    for group, members in groups.items():
        if set(cmds.listRelatives(group, children=True, fullPath=True) or []) != members:
            raise ValueError('Unrecorded children in backup container: ' + group)
    return {'delete': deletable, 'binding_inputs': inputs, 'groups': sorted(groups),
            'output_paths': list(outputs.values()), 'candidate': scene}


def run(payload):
    import maya.cmds as cmds
    started = perf_counter()
    params = payload['parameters']
    build_path = safety.managed(params.get('build_path', ''), suffix={'.json'})
    build = json.loads(build_path.read_text(encoding='utf-8'))
    planned = inspect(cmds, build)
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError('Finalization requires native undo enabled')
    destination = safety.output_path(params.get('output_path'), build['character'], planned['candidate'])
    if params.get('mode') == 'finalize_preview':
        return 'SUCCESS', {'delete_count': len(planned['delete']), 'binding_input_count': len(planned['binding_inputs']),
                           'binding_inputs': planned['binding_inputs'], 'planned_output': str(destination)}
    updated = copy.deepcopy(build)
    # Baselines survive deletion of obsolete source blendShape nodes.
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import blendshape_map
    source_data = {}
    for row in build.get('regional_outputs', []):
        if row.get('blendshape', {}).get('node'):
            source = safety.resolve(cmds, row['source_uuid'])
            for bs in cmds.ls(cmds.listHistory(source) or [], type='blendShape') or []:
                source_data[bs] = blendshape_map.capture(cmds, bs)
    updated['regional_source_blendshapes'] = source_data
    baseline = _geometry(cmds, planned['output_paths'])
    connections = _connections(cmds, planned['output_paths'])
    selection = cmds.ls(selection=True, long=True) or []
    marker, controller_setup = None, None
    chunk = 'ProxyRigFinalize'
    cmds.undoInfo(openChunk=True, chunkName=chunk)
    try:
        marker = cmds.createNode('network', name='cgi_proxy_finalize_transaction')
        nodes = [safety.resolve(cmds, r['uuid']) for r in planned['delete']]
        if nodes:
            cmds.delete(nodes)
        for group in planned['groups']:
            if cmds.listRelatives(group, children=True):
                cmds.rename(group, 'cgi_proxy_binding_inputs')
            else:
                cmds.delete(group)
        for row in planned['binding_inputs']:
            row['path'] = safety.resolve(cmds, row['uuid'])
        if _geometry(cmds, planned['output_paths']) != baseline:
            raise RuntimeError('Finalization changed a delivered surface')
        if _connections(cmds, planned['output_paths']) != connections:
            raise RuntimeError('Finalization changed live deformation input connections')
        if any(cmds.ls(r['uuid']) for r in planned['delete']):
            raise RuntimeError('A disposable source survived finalization')
        if params.get('controller_options'):
            from cgi_pipeline.capabilities.maya.rig.controller_tags.core import apply as prepare_controllers
            controller_setup = prepare_controllers(cmds, {**params['controller_options'], 'mode': 'apply'}, manage_undo=False)
        cmds.delete(marker)
        live = [p for p in selection if cmds.objExists(p)]
        cmds.select(live, replace=True) if live else cmds.select(clear=True)
    except Exception:
        cmds.undoInfo(closeChunk=True)
        if marker is not None and cmds.undoInfo(query=True, undoName=True) == chunk:
            cmds.undo()
        if controller_setup:
            from cgi_pipeline.capabilities.maya.rig.controller_tags.core import restore_host
            restore_host(cmds, controller_setup['host_before'])
        raise
    else:
        cmds.undoInfo(closeChunk=True)
    updated['retained_sources'] = []
    updated['binding_inputs'] = planned['binding_inputs']
    updated['controller_setup'] = controller_setup
    updated['finalization'] = {'candidate_path': planned['candidate'], 'build_path': str(build_path),
                               'deleted_sources': len(planned['delete']), 'binding_input_count': len(planned['binding_inputs']),
                               'output_surfaces_unchanged': True, 'cleanup_seconds': perf_counter() - started}
    from cgi_pipeline.capabilities.maya.asset.save_scene.save_scene import execute as save
    saved = save({'parameters': {'save_path': str(destination)}})
    if saved['status'] != 'SUCCESS':
        if cmds.undoInfo(query=True, undoName=True) == chunk:
            cmds.undo(); cmds.file(rename=planned['candidate'])
        if controller_setup:
            from cgi_pipeline.capabilities.maya.rig.controller_tags.core import restore_host
            restore_host(cmds, controller_setup['host_before'])
        raise RuntimeError('Finalize save failed: ' + saved.get('error', ''))
    updated['output_path'] = saved['output']['output_path']
    updated['finalization']['total_seconds'] = perf_counter() - started
    artifact = safety.write_report(safety.artifact_root(payload, updated['output_path']), 'finalized', updated)
    return 'SUCCESS', {'output_path': updated['output_path'], 'report_path': artifact, **updated['finalization']}
