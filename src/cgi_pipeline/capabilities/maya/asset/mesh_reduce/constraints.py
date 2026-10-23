"""Conservative material-use classification for native Reduce UV constraints."""
from . import geometry as geo


def uv_sensitive(cmds, shading_group):
    """Only a known, constant surface shader proves UV seams expendable.

    Unknown/custom shaders, upstream networks and displacement stay protected.
    UV coordinates/sets are still carried by native Reduce in either case.
    """
    for plug in ('displacementShader', 'volumeShader'):
        if cmds.listConnections(shading_group+'.'+plug, source=True, destination=False):
            return True
    shaders=cmds.listConnections(shading_group+'.surfaceShader', source=True, destination=False) or []
    if len(shaders)!=1 or cmds.nodeType(shaders[0]) not in {'lambert','blinn','phong','phongE','surfaceShader'}:
        return True
    return bool(cmds.listConnections(shaders[0], source=True, destination=False))


def capture(shaders):
    cmds,om,_=geo.modules()
    return [uv_sensitive(cmds,om.MFnDependencyNode(shader).name()) for shader in shaders]


def reduce_flags(cache, faces):
    protected=any(cache['uv_sensitive'][cache['assignment'][f]] for f in faces)
    return {'keepMapBorder':protected}, {'uv_border_protected':protected,
        'reason':'UV-dependent or unknown material' if protected else 'known constant surface materials'}


def face_features(cache):
    """Authored UV/hard-normal discontinuity density, independent of object splits."""
    import numpy as np
    polygons=cache['polygons'];result=np.zeros((len(polygons),2));edges={}
    for face,row in enumerate(polygons):
        sensitive=cache['uv_sensitive'][cache['assignment'][face]]
        for index,(a,b) in enumerate(zip(row,row[1:]+row[:1])):
            corners={a:index,b:(index+1)%len(row)};ordered=tuple(sorted((a,b)))
            uv=tuple(tuple(values[3][face][corners[v]] for v in ordered)
                     if values[2][face] else () for values in cache['uvs'].values())
            normals=np.asarray([cache['normals'][cache['normal_rows'][face][corners[v]]] for v in ordered])
            if ordered in edges:
                old,old_uv,old_normals,old_sensitive=edges.pop(ordered)
                uv_cut=uv!=old_uv;hard_cut=np.max(np.abs(normals-old_normals))>1e-5
                result[face]+=[uv_cut and sensitive,hard_cut]
                result[old]+=[uv_cut and old_sensitive,hard_cut]
            else:edges[ordered]=(face,uv,normals,sensitive)
    for face,_,_,sensitive in edges.values():result[face]+=[sensitive,1.]
    return result/np.asarray([len(p) for p in polygons])[:,None]
