"""Source deformation support masks; no character-specific percentage policy."""
from ..mesh_reduce import blendshape_map,geometry as geo,regions


def live_skin_vertices(cmds, data):
    """Inspect actual target geometry history; neither a BS nor its name suffices."""
    affected=set();skinned={}
    for target in data['targets']:
        for item in target['items']:
            for plug in item['geometry']:
                node=plug.split('.')[0]
                if node not in skinned:
                    skinned[node]=any(cmds.nodeType(n)=='skinCluster'
                                      for n in cmds.listHistory(node,pruneDagObjects=True) or [])
                if skinned[node]:
                    if not item['ids']:raise ValueError('Live skinned target has no established source component map: '+plug)
                    affected.update(item['ids'])
    return affected


def expression_body(row,character,search_options=None,*,keep_face_ratio=.2):
    cmds,om,_=geo.modules();dag,fn=geo.mesh_fn(row['shape']);polygons=regions.face_vertices(fn)
    affected=set()
    for node in row.get('blendshapes',[]):
        affected.update(live_skin_vertices(cmds,blendshape_map.capture(cmds,node)))
    if any(i<0 or i>=fn.numVertices for i in affected):raise ValueError('Expression component map is outside source mesh')
    protected={i for i,p in enumerate(polygons) if affected.intersection(p)}
    remainder=sorted(set(range(fn.numPolygons))-protected);result=[]
    if protected:
        result.append({'name':'live_expression','faces':sorted(protected),'keep_face_ratio':1.,
                       'preserve_border':True,'output_name':character+'_head'})
    if remainder:
        result.append({'name':'reducible_surface','faces':remainder,'keep_face_ratio':keep_face_ratio,
                       'preserve_border':bool(protected),'output_name':character+'_body',
                       **({'search':search_options} if search_options is not None else {})})
    return result
