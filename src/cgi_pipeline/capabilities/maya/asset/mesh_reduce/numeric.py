"""Geometry requests through the existing registered numeric worker/session."""
from __future__ import annotations
import json
import uuid
from pathlib import Path
from time import perf_counter
import numpy as np


def run(operation, arrays, options=None, workdir=None):
    from ...rig.maya_clean_skinweights import dair_solver as runtime
    config = runtime.runtime_config()
    root = runtime.resolve_workdir(workdir) / ('geometry_' + uuid.uuid4().hex)
    root.mkdir()
    request = {'operation': operation, 'options': options or {}, 'request_id': root.name,
               'runtime_fingerprint': config['fingerprint']}
    np.savez(root/'input.npz', **arrays)
    path = root/'request.json'; path.write_text(json.dumps(request), encoding='utf8')
    started = perf_counter()
    completed = runtime._execute(['--request', str(path)], config, root)
    (root/'stdout.log').write_text(completed.stdout, encoding='utf8')
    if completed.returncode:
        (root/'stderr.log').write_text(completed.stderr, encoding='utf8')
        raise RuntimeError('Geometry numerical request failed: '+str(root))
    result = json.loads((root/'result.json').read_text(encoding='utf8'))
    if result['request_id'] != root.name or result['runtime_fingerprint'] != config['fingerprint']:
        raise RuntimeError('Geometry numerical result identity mismatch')
    result['transport_seconds'] = perf_counter()-started
    result['artifact_dir'] = str(root)
    return result


def analyze(entries, workdir=None):
    """Read authoritative deform inputs; no scene mutation or weight preparation."""
    from . import geometry as geo
    from cgi_pipeline.hosts.maya.asset_info_collector import get_deform_input
    from ...rig.maya_clean_skinweights.dair_solver import numeric_session
    if not entries:raise ValueError('Analyze requires explicit source entries')
    cmds, om, _ = geo.modules()
    results=[]
    with numeric_session():
        for entry in entries or []:
            shape=geo.resolve(entry['source'],entry['expected_uuid'])
            visible,original=get_deform_input(entry['source'])
            dag,fn=geo.mesh_fn(original or visible or shape)
            tc,tri=fn.getTriangles()
            arrays={'points':np.asarray(fn.getPoints(om.MSpace.kWorld))[:,:3],
                    'triangles':np.asarray(tri).reshape(-1,3),
                    'polygon_ids':np.repeat(np.arange(fn.numPolygons),np.asarray(tc))}
            measured=run('geometry.group',arrays,{'group_count':entry['group_count']},workdir)
            measured['analysis_path']=str(Path(measured['artifact_dir'])/'result.json')
            measured['groups']=[{'group':g['group'],'face_count':len(g['faces']),'shell_count':len(g['shells']),
                                 'density_range':g['density_range'],'surface_area':g['surface_area']} for g in measured['groups']]
            results.append({'source':entry['source'],'source_uuid':entry['expected_uuid'],**measured})
    return {'rows':results,'scene_modified':False,'construction_nodes_created':0}
