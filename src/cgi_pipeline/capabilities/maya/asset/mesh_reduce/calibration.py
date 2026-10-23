"""Static similarity studies; source rig and skin weights are never modified."""
from __future__ import annotations
from collections import defaultdict
from time import perf_counter
import numpy as np
from . import geometry as geo,regions,ops,numeric


def create_reference(sources,name,parent):
    cmds,om,_=geo.modules()
    snapshots=[geo.snapshot(source) for source in sources]
    joined=geo.concatenate(snapshots)
    node=cmds.createNode('transform',name=name,parent=parent)
    node=cmds.ls(node,long=True)[0];selection=om.MSelectionList();selection.add(node)
    fn=om.MFnMesh()
    fn.create([om.MPoint(*p) for p in joined['points']],joined['face_counts'],joined['face_indices'],
              parent=selection.getDependNode(0))
    restore_uvs(fn,joined)
    shaders=defaultdict(list)
    for face,uid in enumerate(joined['materials']):shaders[uid].append(f'{node}.f[{face}]')
    for uid,faces in shaders.items():cmds.sets(faces,edit=True,forceElement=cmds.ls(uid)[0])
    cmds.setAttr(ops._shape(node)+'.displaySmoothMesh',0)
    geo.verify_clean(joined,geo.snapshot(node))
    return node


def restore_uvs(fn,snapshot):
    for name,corners in snapshot['uvs'].items():
        if name not in fn.getUVSetNames():fn.createUVSet(name)
        labels=snapshot['uv_partition'][name];values={}
        for label,uv in zip(labels,corners):
            if label is not None:values[label]=uv
        lookup={label:i for i,label in enumerate(sorted(values))}
        ordered=[values[label] for label in sorted(values)]
        fn.setUVs([v[0] for v in ordered],[v[1] for v in ordered],name)
        counts=[];indices=[];offset=0
        for width in snapshot['face_counts']:
            assigned=[lookup[v] for v in labels[offset:offset+width] if v is not None]
            counts.append(len(assigned));indices.extend(assigned);offset+=width
        fn.assignUVs(counts,indices,name)


def mesh_arrays(node):
    _,om,_=geo.modules();_,fn=geo.mesh_fn(ops._shape(node))
    counts,ids=fn.getTriangles()
    return {'points':np.asarray(fn.getPoints(om.MSpace.kWorld))[:,:3],
            'triangles':np.asarray(ids,dtype=np.int64).reshape(-1,3),
            'polygon_ids':np.repeat(np.arange(fn.numPolygons),np.asarray(counts))}


def prepare_working(reference,partitions):
    cmds,_,_=geo.modules();work=[]
    for index,specification in enumerate(partitions):
        node,ids=regions.extract(reference,specification['faces'],'cgi_calibration_work_'+str(index))
        history=cmds.polyReduce(node,percentage=0.,vertexMapName='__cgi_source_vertex',
                               cachingReduce=True,**ops.REDUCE)[0]
        work.append({'node':node,'source_ids':ids,'history':history,
                     'factor':specification.get('factor',1.)})
    return work


def evaluate_working(work,keep_ratio,reference_id,workdir):
    cmds,_,_=geo.modules();points=[];triangles=[];mapping=[];offset=0
    for group in work:
        ratio=min(1.,keep_ratio*group['factor'])
        cmds.setAttr(group['history']+'.percentage',100*(1-ratio))
        arrays=mesh_arrays(group['node']);triangles.append(arrays['triangles']+offset)
        points.append(arrays['points']);offset+=len(arrays['points'])
        _,fn=geo.mesh_fn(ops._shape(group['node']));colors=fn.getVertexColors('__cgi_source_vertex')
        ids=[int(round(c.r))*65536+int(round(c.g)) for c in colors]
        if len(ids)!=len(arrays['points']) or min(ids)<0 or max(ids)>=len(group['source_ids']):
            raise RuntimeError('Reduce source vertex map is invalid')
        mapping.extend(group['source_ids'][i] for i in ids)
    result=numeric.run('geometry.similarity',{'points':np.concatenate(points),
        'triangles':np.concatenate(triangles),'source_vertex_ids':np.asarray(mapping)},
        {'reference_id':reference_id},workdir)
    result.update(keep_ratio=keep_ratio,faces=sum(cmds.polyEvaluate(g['node'],face=True) for g in work))
    return result


def copy_result(work,name,parent):
    cmds,_,_=geo.modules();copies=[]
    for index,group in enumerate(work):
        node=ops._copy(group['node'],name+'_piece_'+str(index))
        cmds.polyColorSet(node,delete=True,colorSet='__cgi_source_vertex')
        copies.append({'node':node,'snapshot':geo.snapshot(node),'prepared':{'parent':parent}})
    from .construction import _join
    node=_join(copies,name)
    node=cmds.rename(node,name)
    if (cmds.listRelatives(node,parent=True,fullPath=True) or [None])[0]!=parent:
        node=cmds.parent(node,parent,absolute=True)[0]
    node=cmds.ls(node,long=True)[0]
    if any(cmds.nodeType(n) in ('polyReduce','polyUnite') for n in cmds.listHistory(ops._shape(node)) or []):
        raise RuntimeError('Calibration result retains construction history')
    cmds.setAttr(ops._shape(node)+'.displaySmoothMesh',0)
    return node


def select_next(samples,target):
    """Bracket on observed scores; do not assume or promise strict monotonicity."""
    lower=[s for s in samples if s['profiles'][0]['fscore']<target]
    upper=[s for s in samples if s['profiles'][0]['fscore']>=target]
    if not lower:return max(.0005,min(s['keep_ratio'] for s in samples)*.3)
    if not upper:return min(1.,max(s['keep_ratio'] for s in samples)*2.)
    pairs=[(a,b) for a in lower for b in upper if a['keep_ratio']<b['keep_ratio']]
    if not pairs:return None
    a,b=min(pairs,key=lambda p:p[1]['keep_ratio']-p[0]['keep_ratio'])
    return (a['keep_ratio']+b['keep_ratio'])*.5


def calibrate(reference,partitions,targets,radii,workdir,parent,prefix,*,max_evaluations=36,tolerance=1.):
    started=perf_counter();arrays=mesh_arrays(reference)
    prepared=numeric.run('geometry.similarity_prepare',{'points':arrays['points'],'triangles':arrays['triangles']},
                         {'radii':radii,'samples_per_shell':512,'seed':0},workdir)
    work=prepare_working(reference,partitions);samples=[];rows=[]
    for ratio in (.015,.05,.15,.4,1.):
        samples.append(evaluate_working(work,ratio,prepared['reference_id'],workdir))
    for target in targets:
        for _ in range(7):
            best=min(samples,key=lambda s:abs(s['profiles'][0]['fscore']-target))
            if abs(best['profiles'][0]['fscore']-target)<=tolerance or len(samples)>=max_evaluations:break
            ratio=select_next(samples,target)
            if ratio is None or any(abs(s['keep_ratio']-ratio)<1e-7 for s in samples):break
            samples.append(evaluate_working(work,ratio,prepared['reference_id'],workdir))
        best=min(samples,key=lambda s:(abs(s['profiles'][0]['fscore']-target),s['faces']))
        # Re-evaluate exactly the chosen parameters before materializing a result.
        actual=evaluate_working(work,best['keep_ratio'],prepared['reference_id'],workdir)
        node=copy_result(work,prefix+'_F'+str(target),parent)
        rows.append({'target':target,'score_error':abs(actual['profiles'][0]['fscore']-target),
                     'target_reached':abs(actual['profiles'][0]['fscore']-target)<=tolerance,
                     'node':node,**actual})
    cmds,_,_=geo.modules()
    for group in work:cmds.setAttr(group['node']+'.visibility',False)
    return {'rows':rows,'samples':samples,'seconds':perf_counter()-started,
            'reference_shells':prepared['source_shells'],'working_nodes':len(work),
            'max_evaluations':max_evaluations,'score_tolerance':tolerance,'radius':radii[0]}
