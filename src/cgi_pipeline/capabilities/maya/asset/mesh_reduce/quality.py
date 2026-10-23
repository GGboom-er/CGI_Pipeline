"""Sampled shape feedback before final binding; retry only affected face groups."""
from __future__ import annotations
import math
from time import perf_counter
import numpy as np
from . import geometry as geo, numeric, regions


def validate(value):
    if value is None: return None
    if not isinstance(value,dict): raise ValueError('quality must be an object')
    maximum=value.get('max_error')
    if maximum is not None and (isinstance(maximum,bool) or not isinstance(maximum,(int,float)) or not math.isfinite(maximum) or maximum<=0):
        raise ValueError('quality.max_error must be a positive finite world distance')
    retries=value.get('max_retries',0);factor=value.get('retry_factor',1.5)
    if type(retries) is not int or not 0<=retries<=2: raise ValueError('quality.max_retries must be 0..2')
    if isinstance(factor,bool) or not isinstance(factor,(int,float)) or not math.isfinite(factor) or not 1<factor<=2:
        raise ValueError('quality.retry_factor must be in (1,2]')
    failure=value.get('on_exceeded','error')
    if failure not in ('error','keep_region'): raise ValueError('quality.on_exceeded must be error or keep_region')
    samples=value.get('samples_per_shell',128)
    if type(samples) is not int or not 0<=samples<=4096: raise ValueError('quality.samples_per_shell must be 0..4096')
    return {'max_error':maximum,'max_retries':retries,'retry_factor':factor,'on_exceeded':failure,'samples_per_shell':samples}


def measure(prepared,specification,piece,options):
    cmds,om,_=geo.modules()
    shape=cmds.listRelatives(piece['node'],s=True,ni=True,f=True,type='mesh')[0]
    _,fn=geo.mesh_fn(shape);_,tri=fn.getTriangles()
    points=np.asarray(prepared['points'])[:,:3]
    matrix=np.asarray(cmds.xform(prepared['source'],q=True,ws=True,matrix=True)).reshape(4,4)
    world=(np.column_stack((points,np.ones(len(points))))@matrix)[:,:3]
    selected=np.isin(prepared['triangle_polygon_ids'],specification['faces'])
    arrays={'points':world,'triangles':prepared['triangles'][selected],
            'candidate_points':np.asarray(fn.getPoints(om.MSpace.kWorld))[:,:3],
            'candidate_triangles':np.asarray(tri).reshape(-1,3),'source_vertex_ids':piece['source_vertex_ids']}
    return numeric.run('geometry.compare',arrays,{'samples_per_shell':options['samples_per_shell'],
        'tolerance':options['max_error']},prepared.get('numeric_workdir'))


def _rebuild(prepared,specification,old,ratio):
    cmds,_,_=geo.modules()
    node,ids=regions.extract(prepared['source'],specification['faces'],'cgi_quality_retry',points=prepared['points'])
    node,ids,counts=regions.reduce_region(node,ids,ratio,preserve_border=specification.get('preserve_border',False))
    cmds.delete(old['node'])
    return {'node':node,'source_vertex_ids':ids,'region':specification['name'],**counts}


def check_groups(prepared,plan,made,value):
    """One cached source preparation; other successful groups are never rebuilt."""
    options=validate(value)
    if options is None: return {'enabled':False,'seconds':0.,'rows':[]}
    started=perf_counter();rows=[]
    for index,(specification,piece) in enumerate(zip(plan,made)):
        ratio=specification['keep_face_ratio'];attempts=[];kept=False
        for attempt in range(options['max_retries']+2):
            measured=measure(prepared,specification,piece,options)
            compact={k:v for k,v in measured.items() if k!='shells'}
            attempts.append({'ratio':ratio,**compact})
            maximum=options['max_error']
            failed=measured['missing_shells'] or (maximum is not None and measured['max']>maximum)
            if not failed: break
            if attempt>=options['max_retries']:
                if options['on_exceeded']=='error' or kept: raise RuntimeError('Shape quality exceeded in '+specification['name'])
                ratio=1.;kept=True
            else: ratio=min(1.,ratio*options['retry_factor'])
            piece=_rebuild(prepared,specification,piece,ratio);made[index]=piece
        else: raise RuntimeError('Shape quality did not converge')
        rows.append({'region':specification['name'],'attempts':attempts,'final_keep_ratio':ratio,
                     'kept_original_region':kept,'final_max':measured['max']})
    return {'enabled':True,'seconds':perf_counter()-started,'options':options,'rows':rows,
            'acceptance':'sampled_geometry_only; independent of animation acceptance'}
