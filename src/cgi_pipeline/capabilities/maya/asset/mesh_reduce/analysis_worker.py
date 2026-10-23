"""Existing worker dispatch target; mature libraries own geometric computation."""
import importlib.metadata
import json
from pathlib import Path
import numpy as np
from . import analysis
REFERENCES={}


def run_request(path,request,dependencies):
    root=Path(path).parent
    with np.load(root/'input.npz',allow_pickle=False) as data:
        if request['operation']=='geometry.similarity_prepare':
            from .similarity import SurfaceReference
            reference=SurfaceReference(data['points'],data['triangles'],**request['options'])
            REFERENCES[request['request_id']]=reference
            measured={'reference_id':request['request_id'],'source_shells':len(reference.shells)}
        elif request['operation']=='geometry.similarity':
            reference=REFERENCES[request['options']['reference_id']]
            measured=reference.evaluate(data['points'],data['triangles'],data['source_vertex_ids'])
        elif request['operation']=='geometry.plan':
            from .grouping import plan
            measured=plan(data['points'],data['triangles'],data['polygon_ids'],
                          boundary_features=data['boundary_features'] if 'boundary_features' in data else None,**request['options'])
        elif request['operation']=='geometry.group':
            measured=analysis.group_shells(data['points'],data['triangles'],data['polygon_ids'],
                                          **request['options'])
            np.savez(root/'output.npz',**{k:measured.pop(k) for k in
                     ('triangle_shells','polygon_shells','shell_density')})
        elif request['operation']=='geometry.compare':
            import igl
            labels=data['triangle_shells'] if 'triangle_shells' in data else igl.facet_components(data['triangles'])[1]
            measured=analysis.compare_shells(data['points'],data['triangles'],labels,
                data['candidate_points'],data['candidate_triangles'],data['source_vertex_ids'],**request['options'])
        else:
            raise ValueError('Unknown geometry operation')
    result={**measured,'request_id':request['request_id'],'runtime_fingerprint':request['runtime_fingerprint'],
            'libraries':{n:importlib.metadata.version(n) for n in ('libigl','scikit-learn')}}
    (root/'result.json').write_text(json.dumps(result),encoding='utf8')
    print(json.dumps({'status':'SUCCESS','result':str(root/'result.json')}),flush=True)
