"""Region construction with exact retained-vertex skin and local BS inheritance."""
from __future__ import annotations

from collections import defaultdict
from time import perf_counter

from . import blendshape_map, geometry as geo, regions, skin


def prepare(source, expected_uuid, region_plan, weight_options):
    import numpy as np
    cmds, om, oma = geo.modules()
    shape = geo.resolve(source, expected_uuid)
    if cmds.referenceQuery(source, isNodeReferenced=True):
        raise ValueError('Referenced outputs require a local sandbox source')
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    filters = [n for n in history if cmds.objectType(n, isAType='geometryFilter')]
    skins = [n for n in filters if cmds.nodeType(n) == 'skinCluster']
    blends = [n for n in filters if cmds.nodeType(n) == 'blendShape']
    if len(skins) != 1 or len(blends) > 1 or any(cmds.nodeType(n) not in ('skinCluster', 'blendShape', 'tweak') for n in filters):
        raise ValueError('Mapped reduction supports one skin and at most one local pre-skin blendShape')
    regions.validate(region_plan, cmds.polyEvaluate(shape, face=True))
    context = skin.binding_context(skins[0], shape, require_rest=False)
    data = blendshape_map.capture(cmds, blends[0]) if blends else None
    first = blends[0] if blends else skins[0]
    if blends:
        # Follow only geometry plugs; listHistory also traverses pose drivers.
        upstream = cmds.listConnections(skins[0]+'.input[0].inputGeometry', s=True, d=False, p=True) or []
        visited = set()
        while upstream and upstream[0].split('.')[0] != blends[0]:
            node = upstream[0].split('.')[0]
            if node in visited or cmds.nodeType(node) != 'groupParts':
                raise ValueError('BlendShape is not directly before the native skin')
            visited.add(node)
            upstream = cmds.listConnections(node+'.inputGeometry', s=True, d=False, p=True) or []
        if not upstream:
            raise ValueError('Cannot establish pre-skin expression order')
    selected = om.MSelectionList(); selected.add(first+'.input[0].inputGeometry')
    mesh = om.MFnMesh(selected.getPlug(0).asMObject())
    points = mesh.getPoints()
    from . import mesh_io
    input_normals=mesh_io.normal_state(mesh,om.MSpace.kObject)
    triangle_counts,triangle_ids=mesh.getTriangles()
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import dair_engine as engine
    weight_start = perf_counter()
    weight_timings={}
    planned = engine._prepare(skins[0], weight_options, cmds, om, oma, engine.ng_engine.MAX_WEIGHT_VALUES,timings=weight_timings)
    planned['metrics']['timing_seconds']=weight_timings
    planned['expected'] = np.asarray(planned['expected'])
    return {'source': source, 'shape': shape, 'context': context, 'blendshape': data,
            'points': points, 'weights': planned, 'weight_report': engine._summarize(planned),
            'input_normals':input_normals,
            'triangles':np.asarray(triangle_ids).reshape(-1,3),
            'triangle_polygon_ids':np.repeat(np.arange(mesh.numPolygons),np.asarray(triangle_counts)),
            'numeric_workdir':weight_options.get('solver_workdir'),
            'weight_seconds': perf_counter()-weight_start}


def apply(prepared, region_plan, prefix, quality_options=None):
    return apply_batch([(prepared,region_plan,quality_options)],prefix)


def apply_batch(entries,prefix):
    """Few static face groups across sources; one bind per compatible output."""
    cmds, _, _ = geo.modules()
    start = perf_counter()
    from .quality import check_groups
    from .construction import _join,bind_pieces
    grouped = defaultdict(list)
    quality=[]
    for index,(prepared,region_plan,quality_options) in enumerate(entries):
        made=regions.construct(prepared['source'],region_plan,prefix+'_'+str(index),points=prepared['points'],workdir=prepared.get('numeric_workdir'),normal_state=prepared.get('input_normals'))
        quality.append({'source':prepared['source'],**check_groups(prepared,region_plan,made,quality_options)})
        parent=(cmds.listRelatives(prepared['source'],p=True,f=True) or [None])[0]
        for specification,row in zip(region_plan,made):
            row.update(weights=prepared['weights'],snapshot=geo.snapshot(row['node']),
                       prepared={'parent':parent},source_data=prepared)
            grouped[specification['output_name']].append(row)
    outputs = []
    for name, pieces in grouped.items():
        source_data={p['source_data']['source']:p['source_data'] for p in pieces}
        contexts=[p['context'] for p in source_data.values()]
        if len({p['prepared']['parent'] for p in pieces})!=1:
            raise ValueError('Regional output must preserve one common parent')
        if len(source_data)>1 and any(p['blendshape'] for p in source_data.values()):
            raise ValueError('Regional multi-source output does not combine expression systems')
        signatures=[geo.visibility_signature(s) for s in source_data]
        if any(s!=signatures[0] for s in signatures):
            raise ValueError('Regional output has different display switching')
        ids = [v for piece in pieces for v in piece['source_vertex_ids']]
        combined=_join(pieces,prefix+'_'+name)
        combined = cmds.ls(combined, l=True)[0]
        first=next(iter(source_data.values()))
        binding_pieces=pieces
        if len(source_data)==1 and len(pieces)>1:
            from . import mesh_io
            cache=regions.capture(first['source'],points=first['points'])
            snapshot=geo.snapshot(combined);snapshot['normal_state']=mesh_io.capture_state(combined)
            stitched,new_ids=mesh_io.stitch_snapshot(snapshot,ids,cache)
            if len(new_ids)!=len(ids):
                previous=combined;combined=mesh_io.create(stitched,prefix+'_'+name+'_stitched',pieces[0]['prepared']['parent'])
                cmds.delete(previous);ids=new_ids
                binding_pieces=[{'weights':first['weights'],'source_vertex_ids':ids}]
        native_skin,calls=bind_pieces(contexts,combined,binding_pieces)
        bs=blendshape_map.restore(cmds,first['blendshape'],combined,ids) if first['blendshape'] else {}
        ranges=[];offset=0
        for piece in pieces:
            count=len(piece['source_vertex_ids'])
            ranges.append({'source_uuid':cmds.ls(piece['source_data']['source'],uuid=True)[0],
                           'start':offset,'end':offset+count});offset+=count
        if len(source_data)==1:
            ranges=[{'source_uuid':cmds.ls(first['source'],uuid=True)[0],'start':0,'end':len(ids)}]
        source_ids=[cmds.ls(s,uuid=True)[0] for s in source_data]
        outputs.append({'result': combined, 'result_uuid': cmds.ls(combined, uuid=True)[0],
            'output_name': name, 'source_vertex_ids': ids, 'skin_cluster': native_skin,
            'source_uuid':source_ids[0],'source_uuids':source_ids,'source_ranges':ranges,
            'weight_write_calls':calls,
            'blendshape': bs, 'regions': [{k: p[k] for k in ('region','before_faces','after_faces')} | ({'search':p['search']} if 'search' in p else {}) for p in pieces],
            'faces': int(cmds.polyEvaluate(combined, face=True))})
    return {'outputs': outputs, 'geometry_seconds': perf_counter()-start,'quality':quality,
            'weight_report':[p['weight_report'] for p,_,_ in entries]}


def reduce_regions(entries, weight_options, character=''):
    """Standalone registered API boundary; same region engine as Proxy build."""
    from . import ops
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights.dair_solver import numeric_session
    cmds, _, _ = geo.modules()
    if not entries or not weight_options:
        raise ValueError('regional mode requires entries and explicit weight_cleanup options')
    names = sorted({r['output_name'] for entry in entries for r in entry['regions']})
    for name in names: ops._name(name)
    from .quality import validate as validate_quality
    for entry in entries: validate_quality(entry.get('quality'))
    ops._names_available(names)
    with numeric_session():
        prepared = [prepare(e['source'], e['expected_uuid'], e['regions'], weight_options) for e in entries]
        snapshots = [{'source':p['source'], 'snapshot':geo.snapshot(p['source'],p['context']['skin'])} for p in prepared]
        def mutate():
            result=[]
            made=apply_batch([(p,e['regions'],e.get('quality')) for p,e in zip(prepared,entries)],'cgi_regionBuild')
            for row in made['outputs']:
                node=cmds.rename(row['result'],row['output_name'])
                node=cmds.ls(node,l=True)[0]
                source=geo.modules()[0].ls(row['source_uuid'],long=True)[0]
                geo.copy_visibility(geo.visibility_signature(source),node)
                row.update(result=node,source=source,quality=made['quality'])
                result.append(row)
            return result
        return ops._transaction('maya.asset.mesh_reduce.regional',snapshots,mutate,character)
