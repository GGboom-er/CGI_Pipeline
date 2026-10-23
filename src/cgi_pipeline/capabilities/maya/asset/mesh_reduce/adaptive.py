"""Pool geometry before grouping; keep source weight identity through one bind."""
from __future__ import annotations
from time import perf_counter
import numpy as np
from . import calibration,cached_reduce,constraints,geometry as geo,mesh_io,numeric,ops,regions


def plan_groups(arrays, options, workdir):
    """Agent partitions take precedence; automatic units are whole shells."""
    explicit = options.get('work_groups', [])
    if explicit:
        from .search import validate_work_groups
        validate_work_groups(explicit)
        expected=set(map(int,arrays['polygon_ids']))
        if {f for group in explicit for f in group['faces']} != expected:
            raise ValueError('Visual work groups must cover source faces exactly once')
        groups=[{**g,'preserve':g['reduction']==0,'preserve_border':True,'units':1} for g in explicit]
        return {'groups':groups,'source_shells':None,'analysis_units':len(groups),'seconds':0.,
                'continuous_scope':'Agent supplied source-face regions; seams stitched by source identity'}
    return numeric.run('geometry.plan', arrays,
        {'minimal_faces':options.get('minimal_faces',12),
         'target_keep_ratio':1-options.get('initial_reduction',90)/100,
         'partition_continuous':options.get('mode')!='visual'}, workdir)


def reduce_partition(source,specification,workdir,prefix,cache):
    """Use the ordinary geometry grouping/search inside a protected region."""
    options=specification['search'];points=np.asarray(cache['points'])[:,:3]
    world=(np.column_stack((points,np.ones(len(points))))@np.asarray(cache['matrix']).reshape(4,4))[:,:3]
    polygons=np.repeat(np.arange(len(cache['polygons'])),np.asarray(cache['triangle_counts']))
    selected=np.isin(polygons,specification['faces'])
    arrays={'points':world,'triangles':np.asarray(cache['triangles']).reshape(-1,3)[selected],
            'polygon_ids':polygons[selected],'boundary_features':constraints.face_features(cache)}
    planned=plan_groups(arrays,options,workdir)
    for group in planned['groups']:
        group['preserve_border']=group['preserve_border'] or specification.get('preserve_border',False)
    made=[cached_reduce.solve(source,g,options,workdir,prefix+'_'+g['name'],source_cache=cache) for g in planned['groups']]
    cmds,_,_=geo.modules()
    for piece in made:piece.update(snapshot=geo.snapshot(piece['node']),prepared={'parent':cache['parent']})
    from .construction import _join
    node=_join(made,prefix+'_joined')
    ids=[v for p in made for v in p['source_vertex_ids']]
    if len(made)>1:
        snapshot=geo.snapshot(node);snapshot['normal_state']=mesh_io.capture_state(node)
        snapshot,stitched_ids=mesh_io.stitch_snapshot(snapshot,ids,cache)
        if len(stitched_ids)!=len(ids):
            previous=node;node=mesh_io.create(snapshot,prefix+'_stitched',cache['parent']);cmds.delete(previous)
        ids=stitched_ids
    return {'node':node,'source_vertex_ids':ids,
            'before_faces':sum(p['before_faces'] for p in made),'after_faces':int(cmds.polyEvaluate(node,face=True)),
            'search':{'groups':[{k:v for k,v in g.items() if k!='faces'}|{'search':p['search']}
                               for g,p in zip(planned['groups'],made)],'grouping_seconds':planned['seconds'],
                      'evaluations':sum(p['search']['evaluations'] for p in made)}}


def construct(prepared,options,workdir,prefix):
    cmds,_,_=geo.modules();started=perf_counter();snapshots=[p['snapshot'] for p in prepared]
    source=geo.concatenate(snapshots)
    states=[p['snapshot'].get('normal_state') or mesh_io.capture_state(p['entry']['source']) for p in prepared]
    source['normal_state']={key:[v for state in states for v in state[key]] for key in states[0]}
    if len(prepared)==1:
        first=prepared[0];original=first['entry']['source']
        cache=regions.capture(original,points=first.get('input_points'))
        if first.get('normal_state') is not None:cache['normal_state']=first['normal_state']
        reference,_=regions.extract(original,list(range(len(cache['polygons']))),prefix+'_reference',source_cache=cache)
    else:
        reference=mesh_io.create(source,prefix+'_reference',prepared[0]['parent'])
    cmds.setAttr(reference+'.visibility',False)
    arrays=calibration.mesh_arrays(reference)
    cache=regions.capture(reference)
    arrays['boundary_features']=constraints.face_features(cache)
    planned=plan_groups(arrays,options,workdir)
    made=[cached_reduce.solve(reference,g,options,workdir,prefix+'_'+g['name'],source_cache=cache) for g in planned['groups']]
    for piece in made:
        piece.update(snapshot=geo.snapshot(piece['node']),prepared={'parent':prepared[0]['parent']})
    from .construction import _join
    joined=_join(made,prefix+'_joined');actual=geo.snapshot(joined)
    actual['normal_state']=mesh_io.capture_state(joined)
    ids=np.asarray([v for piece in made for v in piece['source_vertex_ids']],dtype=np.int64)
    if len(ids)!=len(actual['points']) or np.max(np.abs(np.asarray(actual['points'])-np.asarray(source['points'])[ids]))>1e-5:
        raise RuntimeError('Pooled source vertex positions or concatenation order changed')
    actual,ids=mesh_io.stitch_snapshot(actual,ids.tolist(),cache)
    ids=np.asarray(ids,dtype=np.int64)
    ends=np.cumsum([len(p['snapshot']['points']) for p in prepared]);starts=np.r_[0,ends[:-1]]
    owners=np.searchsorted(ends,ids,side='right');order=np.argsort(owners,kind='stable')
    inverse=np.empty(len(order),dtype=np.int64);inverse[order]=np.arange(len(order))
    actual['points']=[actual['points'][i] for i in order]
    actual['face_indices']=[int(inverse[i]) for i in actual['face_indices']]
    result=mesh_io.create(actual,prefix+'_result',prepared[0]['parent']);cmds.delete(joined,reference)
    # Virtual source pieces allocate no Maya models. Reordered vertices allow
    # bounded bulk writes to span source boundaries rather than one write/model.
    pieces=[];after_faces=np.zeros(len(prepared),dtype=int)
    offsets=np.r_[0,np.cumsum(actual['face_counts'])[:-1]]
    sorted_owners=owners[order];face_owners=sorted_owners[np.asarray(actual['face_indices'])[offsets]]
    after_faces+=np.bincount(face_owners,minlength=len(prepared))
    for index,p in enumerate(prepared):
        local=ids[order][sorted_owners==index]-starts[index]
        snapshot={**p['snapshot'],'counts':{'faces':int(after_faces[index]),'vertices':len(local)}}
        pieces.append({'source_vertex_ids':local.tolist(),'weights':p['weights'],'prepared':p,
                       'snapshot':snapshot,'before_faces':p['snapshot']['counts']['faces'],
                       'after_faces':int(after_faces[index])})
    return result,pieces,{'groups':[{k:v for k,v in g.items() if k!='faces'} | {'search':p['search']}
                for g,p in zip(planned['groups'],made)],'source_shells':planned['source_shells'],
                'analysis_units':planned['analysis_units'],'grouping_seconds':planned['seconds'],
                'geometry_seconds':perf_counter()-started,'scope':planned['continuous_scope']}
