"""Prune source arrays, reduce static geometry, bind each final output once."""
from __future__ import annotations

from time import perf_counter

from . import geometry as geo, ops, regions, skin


def weight_blocks(pieces, influence_ids, max_values=1000000):
    """Yield bounded dense writes without building a whole union-bone matrix."""
    import numpy as np
    lookup = {uid: i for i, uid in enumerate(influence_ids)}
    size = max(1, max_values // len(influence_ids))
    offset=0;filled=0;values=np.zeros((size,len(influence_ids)));blends=np.zeros(size)
    for piece in pieces:
        data = piece['weights']
        ids = piece['source_vertex_ids']
        columns = [lookup.get(r['uuid']) for r in data['influences']]
        kept = [i for i, column in enumerate(columns) if column is not None]
        destination = [columns[i] for i in kept]
        begin=0
        while begin<len(ids):
            count=min(size-filled,len(ids)-begin);selected=ids[begin:begin+count]
            values[filled:filled+count,destination]=data['expected'][selected][:,kept]
            blends[filled:filled+count]=np.asarray(data['blend_weights'])[selected]
            filled+=count;begin+=count
            if filled==size:
                yield offset,values,blends
                offset+=filled;filled=0;values=np.zeros((size,len(influence_ids)));blends=np.zeros(size)
    if filled:yield offset,values[:filled],blends[:filled]


def bind_pieces(contexts, target, pieces):
    import numpy as np
    cmds, om, _ = geo.modules()
    skin.compatible(contexts)
    bindings = {uid: data for context in contexts for uid, data in context['bindings'].items()}
    active = set()
    for piece in pieces:
        weights = piece['weights']
        columns = np.any(weights['expected'][piece['source_vertex_ids']] > 0, axis=0)
        active.update(r['uuid'] for r, used in zip(weights['influences'], columns) if used)
    bindings = {uid: row for uid, row in bindings.items() if uid in active}
    source_context = {**contexts[0], 'bindings': bindings}
    names = [r['name'] for r in bindings.values()]
    native = cmds.skinCluster(names, target, toSelectedBones=True, normalizeWeights=1,
                             skinMethod=int(source_context['settings']['skinningMethod']),
                             maximumInfluences=len(names), obeyMaxInfluences=False)[0]
    cmds.setAttr(native+'.maintainMaxInfluences', False)
    fn = skin.skin_fn(native)
    path = om.MDagPath.getAPathTo(fn.getOutputGeometry()[0])
    order = [cmds.ls(p.fullPathName(), uuid=True)[0] for p in fn.influenceObjects()]
    calls = 0
    for begin, values, blends in weight_blocks(pieces, order):
        comp = om.MFnSingleIndexedComponent()
        component = comp.create(om.MFn.kMeshVertComponent)
        comp.addElements(range(begin, begin+len(values)))
        fn.setWeights(path, component, om.MIntArray(range(len(order))), om.MDoubleArray(values.ravel()), False)
        fn.setBlendWeights(path, component, om.MDoubleArray(blends))
        calls += 1
    skin.restore_binding_context(native, source_context)
    return native, calls


def _prepare(entry, options):
    cmds, om, oma = geo.modules()
    shape = geo.resolve(entry['source'], entry['expected_uuid'])
    native = geo.protect_dependencies(entry['source'], shape)
    context = skin.binding_context(native, shape, require_rest=False) if native else None
    snapshot = geo.snapshot(entry['source'])
    snapshot['skin_cluster'] = native
    input_points,normal_state=None,None
    if context:
        # Always construct in the skin's input space. A current pose is not an
        # error and must never be baked then deformed a second time.
        selected=om.MSelectionList();selected.add(native+'.input[0].inputGeometry')
        input_mesh=om.MFnMesh(selected.getPlug(0).asMObject())
        input_points=input_mesh.getPoints()
        counts,vertices=input_mesh.getVertices()
        if list(counts)!=snapshot['face_counts'] or list(vertices)!=snapshot['face_indices']:
            raise ValueError('Skin input/output topology differs: '+entry['source'])
        from . import mesh_io
        normal_state=mesh_io.normal_state(input_mesh,om.MSpace.kObject)
        matrix=om.MMatrix(cmds.xform(entry['source'],q=True,ws=True,matrix=True))
        snapshot['points']=[tuple(p*matrix)[:3] for p in input_points]
        world_normals={**normal_state,'normal_corners':[tuple(om.MVector(*n).transformAsNormal(matrix)) for n in normal_state['normal_corners']]}
        snapshot['normal_state']=world_normals
    planned, seconds = None, 0.
    weight_timings = {}
    if context:
        from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import dair_engine
        started = perf_counter()
        planned = dair_engine._prepare(native, options, cmds, om, oma, dair_engine.ng_engine.MAX_WEIGHT_VALUES, timings=weight_timings)
        planned['metrics']['timing_seconds'] = weight_timings
        seconds = perf_counter()-started
    return {'entry': entry, 'snapshot': snapshot, 'skin': context, 'weights': planned,
            'input_points':input_points,'normal_state':normal_state,
            'weight_seconds': seconds, 'parent': (cmds.listRelatives(entry['source'], p=True, f=True) or [None])[0]}


def _piece(prepared, name):
    cmds, om, _ = geo.modules()
    entry, snapshot = prepared['entry'], prepared['snapshot']
    if prepared.get('input_points') is not None:
        cache=regions.capture(entry['source'],points=prepared['input_points'])
        cache['normal_state']=prepared['normal_state']
        working,ids=regions.extract(entry['source'],list(range(snapshot['counts']['faces'])),name,source_cache=cache)
    else:
        working = ops._copy(entry['source'], name)
        ids = list(range(snapshot['counts']['vertices']))
    node, ids, counts = regions.reduce_region(working, ids, entry['keep_face_ratio'])
    actual = geo.snapshot(node)
    # The native vertex-map contract is accepted only with actual point evidence.
    if any(not geo._near(point, snapshot['points'][old], 1e-5) for point, old in zip(actual['points'], ids)):
        raise RuntimeError('Reduce vertex map does not preserve source points: '+entry['source'])
    return {'node': node, 'source_vertex_ids': ids, 'weights': prepared['weights'],
            'snapshot': actual, 'prepared': prepared, **counts}


def _join(pieces, name):
    cmds, _, _ = geo.modules()
    if len(pieces) == 1:
        return pieces[0]['node']
    nodes = [p['node'] for p in pieces]
    result = cmds.polyUnite(nodes, constructionHistory=False, mergeUVSets=1)[0]
    parent = pieces[0]['prepared']['parent']
    result = cmds.parent(result, parent, absolute=True)[0] if parent else cmds.parent(result, world=True)[0]
    result = cmds.rename(result, name)
    expected = geo.concatenate([p['snapshot'] for p in pieces])
    geo.verify_clean(expected, geo.snapshot(result))
    cmds.delete([n for n in nodes if cmds.objExists(n)])
    return cmds.ls(result, l=True)[0]


def construct_groups(groups, options, *, character='', manage_undo=True):
    """One transaction, arrays before topology; no temporary source/output skins."""
    cmds, _, _ = geo.modules()
    if not groups or not options:
        raise ValueError('Construction requires explicit groups and weight cleanup options')
    names = [ops._name(g['output_name']) for g in groups]
    ops._names_available(names)
    sources = [e['source'] for g in groups for e in g['entries']]
    if len(set(sources)) != len(sources):
        raise ValueError('Construction sources must be distinct')
    for group in groups:
        if not group['entries']:
            raise ValueError('Empty construction group')
        for entry in group['entries']:
            ops.validate_entry({**entry, 'output_name': group['output_name'], 'preserve_skin': 'auto'})
        if group.get('search') is not None:
            from .search import validate as validate_search
            validate_search(group['search'])
    rows, group_rows, weight_rows = [], [], []
    timings = {'source_weights': 0., 'geometry': 0., 'transfer_repair': 0., 'join': 0., 'bind': 0., 'total': 0.}
    started = perf_counter()
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import dair_engine
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights.dair_solver import numeric_session
    def mutate():
        for index, group in enumerate(groups):
            prepared = [_prepare(entry, options) for entry in group['entries']]
            if len({p['parent'] for p in prepared}) != 1:
                raise ValueError('Construction group must preserve one common parent')
            if any(p['snapshot']['visibility'] != prepared[0]['snapshot']['visibility'] for p in prepared):
                raise ValueError('Construction group has different display switching')
            skin.compatible([p['skin'] for p in prepared])
            timings['source_weights'] += sum(p['weight_seconds'] for p in prepared)
            weight_rows.extend(dair_engine._summarize(p['weights']) for p in prepared if p['weights'] is not None)
            t = perf_counter()
            study=None
            if group.get('search') is not None:
                from .adaptive import construct
                result,pieces,study=construct(prepared,group['search'],options.get('solver_workdir'),character+'_adaptive_'+str(index))
            else:
                pieces = [_piece(p, character+'_construction_'+str(index)+'_'+str(i)) for i, p in enumerate(prepared)]
            timings['geometry'] += perf_counter()-t
            t = perf_counter()
            if study is None:result = _join(pieces, group['output_name'])
            timings['join'] += perf_counter()-t
            native, calls = '', 0
            t = perf_counter()
            if prepared[0]['skin']:
                native, calls = bind_pieces([p['skin'] for p in prepared], result, pieces)
                cmds.setAttr(native+'.maxInfluences', options['max_influences'])
                cmds.setAttr(native+'.maintainMaxInfluences', True)
            timings['bind'] += perf_counter()-t
            geo.copy_visibility(prepared[0]['snapshot']['visibility'], result)
            uid = cmds.ls(result, uuid=True)[0]
            group_rows.append({'result': result, 'result_uuid': uid, 'skin_cluster': native,
                               'output_name': group['output_name'], 'skin_nodes_created': int(bool(native)),
                               'weight_write_calls': calls, 'source_count': len(pieces),
                               **({'search':study} if study is not None else {})})
            if options.get('solver_workdir'):
                import json
                from pathlib import Path
                event={'output':group['output_name'],'sources':len(prepared),'faces':sum(p['after_faces'] for p in pieces),
                       'search_groups':len(study['groups']) if study else 0,'elapsed_seconds':perf_counter()-started}
                with (Path(options['solver_workdir'])/'construct_progress.jsonl').open('a',encoding='utf8') as stream:
                    stream.write(json.dumps(event)+'\n')
            for piece in pieces:
                p = piece['prepared']
                rows.append({'source': p['entry']['source'], 'source_uuid': p['entry']['expected_uuid'],
                    'result': result, 'result_uuid': uid, 'before': geo.summary(p['snapshot']),
                    'after': geo.summary(piece['snapshot']), 'keep_face_ratio': p['entry']['keep_face_ratio'],
                    'actual_face_ratio': piece['after_faces']/piece['before_faces'],
                    'source_vertex_ids': piece['source_vertex_ids'], 'comparison_basis': 'retained_vertex_map'})
        return group_rows
    with numeric_session():
        transaction = ops._transaction('maya.asset.mesh_reduce.construct', [], mutate, character, manage_undo)
    timings['total'] = perf_counter()-started
    return {'rows': rows, 'groups': group_rows, 'weights': weight_rows, 'timing_seconds': timings,
            'transaction': transaction['transaction'],
            'skin_nodes_created': sum(r['skin_nodes_created'] for r in group_rows),
            'weight_write_calls': sum(r['weight_write_calls'] for r in group_rows)}
