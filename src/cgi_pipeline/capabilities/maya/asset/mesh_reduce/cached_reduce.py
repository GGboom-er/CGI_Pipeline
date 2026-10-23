"""One Reduce node, cached source queries, broad score band and a hard budget."""
from __future__ import annotations
import hashlib
from time import perf_counter
import numpy as np
from . import calibration,constraints,geometry as geo,numeric,ops,regions,search


def _arrays(node):
    arrays=calibration.mesh_arrays(node);_,fn=geo.mesh_fn(ops._shape(node))
    colors=fn.getVertexColors('__cgi_source_vertex')
    mapping=np.asarray([int(round(c.r))*65536+int(round(c.g)) for c in colors],dtype=np.int64)
    return arrays,mapping


def solve(reference,specification,options,workdir,name,*,points=None,source_cache=None):
    options=search.validate(options);started=perf_counter();cmds,_,_=geo.modules()
    cache=source_cache if source_cache is not None else regions.capture(reference,points=points)
    node,source_ids=regions.extract(reference,specification['faces'],name+'_work',source_cache=cache)
    flags,policy=constraints.reduce_flags(cache,specification['faces'])
    original=calibration.mesh_arrays(node);before_faces=cmds.polyEvaluate(node,face=True)
    tri=original['points'][original['triangles']]
    area=float(np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1).sum()*.5)
    identity={'source_area':area,'candidate_area':area,'source_coverage':100.,'candidate_coverage':100.}
    if specification.get('preserve'):
        return {'node':node,'source_vertex_ids':source_ids,'before_faces':before_faces,'after_faces':before_faces,
                'search':{'stop_reason':'minimal_source','score':100.,'evaluations':0,**identity,'seconds':perf_counter()-started}}
    if options.get('mode') == 'visual':
        amount = specification.get('reduction', options['initial_reduction'])
        clean, mapping, counts = regions.reduce_region(node, source_ids, 1-amount/100.,
            preserve_border=specification.get('preserve_border',False), reduce_flags=flags)
        return {'node':clean,'source_vertex_ids':mapping,**counts,
                'search':{'mode':'visual','reduction':amount,'evaluations':0,'reduce_calls':int(amount>0),
                          'stop_reason':'awaiting_visual_review','constraints':policy,
                          'seconds':perf_counter()-started}}
    prepared=numeric.run('geometry.similarity_prepare',{'points':original['points'],'triangles':original['triangles']},
                        {'radii':[options['radius']],'samples_per_shell':options['samples_per_shell'],'seed':0},workdir)
    history=cmds.polyReduce(node,percentage=0.,vertexMapName='__cgi_source_vertex',cachingReduce=True,
                **{**ops.REDUCE,**flags,'keepBorderWeight':1. if specification.get('preserve_border') else .5})[0]
    rows=[];seen=set();reason='budget_best_safe'
    for _ in range(options['max_evaluations']):
        percentage=search.next_reduction(rows,options)
        if percentage is None:reason='parameter_plateau';break
        cmds.setAttr(history+'.percentage',percentage);arrays,mapping=_arrays(node)
        if len(mapping)!=len(arrays['points']) or mapping.min()<0 or mapping.max()>=len(original['points']):
            raise RuntimeError('Native Reduce source map changed')
        digest=hashlib.sha256(arrays['triangles'].tobytes()+mapping.tobytes()).hexdigest()
        if digest in seen:reason='native_geometry_plateau';break
        seen.add(digest)
        measured=numeric.run('geometry.similarity',{'points':arrays['points'],'triangles':arrays['triangles'],
            'source_vertex_ids':mapping},{'reference_id':prepared['reference_id']},workdir)
        row={'reduction':percentage,'score':measured['profiles'][0]['fscore'],'faces':int(cmds.polyEvaluate(node,face=True)),
             'missing_shells':measured['missing_shells'],'numeric_seconds':measured['seconds'],
             'source_area':measured['source_area'],'candidate_area':measured['candidate_area'],
             'source_coverage':measured['profiles'][0]['source_coverage'],'candidate_coverage':measured['profiles'][0]['candidate_coverage']}
        rows.append(row)
        if search.acceptable(row,options):reason='score_band';break
    selected=search.choose(rows,options)
    if selected is None:
        cmds.setAttr(history+'.percentage',0.);reason='original_fallback'
        selected={'reduction':0.,'score':100.,'faces':before_faces,'missing_shells':0,**identity}
    else:cmds.setAttr(history+'.percentage',selected['reduction'])
    arrays,mapping=_arrays(node)
    if np.max(np.abs(arrays['points']-original['points'][mapping]))>1e-5:raise RuntimeError('Native retained point map failed')
    clean=ops._copy(node,name+'_clean');cmds.polyColorSet(clean,delete=True,colorSet='__cgi_source_vertex')
    cmds.delete(node)
    return {'node':clean,'source_vertex_ids':[source_ids[int(i)] for i in mapping],
            'before_faces':before_faces,'after_faces':int(cmds.polyEvaluate(clean,face=True)),
            'search':{**selected,'options':options,'constraints':policy,'evaluations':len(rows),'attempts':rows,
                      'stop_reason':reason,'seconds':perf_counter()-started}}
