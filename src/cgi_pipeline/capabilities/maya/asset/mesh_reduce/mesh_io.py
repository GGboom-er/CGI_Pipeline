"""Create a clean world-space mesh from the shared geometry snapshot format."""
from collections import Counter, defaultdict
from . import geometry as geo


def capture_normals(node):
    cmds,om,_=geo.modules();shape=cmds.listRelatives(node,shapes=True,noIntermediate=True,fullPath=True,type='mesh')[0]
    _,fn=geo.mesh_fn(shape)
    values=fn.getNormals(om.MSpace.kWorld);counts,ids=fn.getNormalIds()
    return [tuple(values[int(i)]) for i in ids]


def normal_state(fn, space):
    """Keep smoothing and authored locks, rather than locking every corner."""
    values=fn.getNormals(space);counts,ids=fn.getNormalIds()
    polygons=[];offset=0
    sizes,vertices=fn.getVertices()
    smooth={tuple(sorted(fn.getEdgeVertices(e))):fn.isEdgeSmooth(e) for e in range(fn.numEdges)}
    for size in sizes:
        row=list(vertices[offset:offset+size]);polygons.append(row);offset+=size
    locked=[fn.isNormalLocked(int(i)) for i in ids]
    normals=[tuple(values[int(i)]) for i in ids]
    return {'smooth_corners':[smooth[tuple(sorted((a,b)))] for row in polygons for a,b in zip(row,row[1:]+row[:1])],
            'locked_corners':locked,'normal_corners':normals}


def capture_state(node):
    cmds,om,_=geo.modules()
    shape=cmds.listRelatives(node,shapes=True,noIntermediate=True,fullPath=True,type='mesh')[0]
    _,fn=geo.mesh_fn(shape)
    return normal_state(fn,om.MSpace.kWorld)


def restore_state(fn, state, counts, indices):
    cmds,om,_=geo.modules();edges={};offset=0
    for size in counts:
        row=list(indices[offset:offset+size])
        for j,(a,b) in enumerate(zip(row,row[1:]+row[:1])):
            key=tuple(sorted((int(a),int(b))));value=bool(state['smooth_corners'][offset+j])
            # A true hard edge wins if the two corner records disagree.
            edges[key]=edges.get(key,True) and value
        offset+=size
    locked=[i for i,value in enumerate(state['locked_corners']) if value]
    if locked:
        faces=[i for i,count in enumerate(counts) for _ in range(count)]
        fn.setFaceVertexNormals([om.MVector(*state['normal_corners'][i]) for i in locked],
                               [faces[i] for i in locked],[indices[i] for i in locked],om.MSpace.kObject)
    # setFaceVertexNormals can harden edges in Maya. Restore smoothing LAST.
    edge_ids=list(range(fn.numEdges))
    smooths=[edges[tuple(sorted(fn.getEdgeVertices(edge)))] for edge in edge_ids]
    fn.setEdgeSmoothings(edge_ids,smooths)
    fn.cleanupEdgeSmoothing()


def stitch_snapshot(snapshot, source_ids, cache):
    """Undo temporary density cuts by source identity, never by proximity.

    Original bow-tie vertices remain split. UVs/materials/normals are corner
    data and stay in face order; the returned map also drives skin and BS.
    """
    from .regions import corner_mapping
    fans,_=corner_mapping(cache['polygons'],list(range(len(cache['polygons']))))
    fan_counts=Counter(fans);lookup={};points=[];ids=[];remap=[]
    for old,(point,source) in enumerate(zip(snapshot['points'],source_ids)):
        key=('source',source) if fan_counts[source]==1 else ('split',old)
        if key not in lookup:
            lookup[key]=len(points);points.append(point);ids.append(source)
        index=lookup[key]
        if any(abs(a-b)>1e-5 for a,b in zip(points[index],point)):
            raise RuntimeError('Source-identical cut vertices changed position')
        remap.append(index)
    if len(points)==len(source_ids):return snapshot,source_ids
    result={**snapshot,'points':points,'face_indices':[remap[i] for i in snapshot['face_indices']]}
    result['counts']={**snapshot['counts'],'vertices':len(points)}
    state={key:list(value) for key,value in snapshot['normal_state'].items()}
    original_edges={};offset=0
    for row in cache['polygons']:
        for j,(a,b) in enumerate(zip(row,row[1:]+row[:1])):
            original_edges[tuple(sorted((a,b)))]=cache['normal_state']['smooth_corners'][offset+j]
        offset+=len(row)
    offset=0
    for size in result['face_counts']:
        row=result['face_indices'][offset:offset+size]
        for j,(a,b) in enumerate(zip(row,row[1:]+row[:1])):
            key=tuple(sorted((ids[a],ids[b])))
            if key in original_edges:state['smooth_corners'][offset+j]=original_edges[key]
        offset+=size
    result['normal_state']=state
    return result,ids


def create(snapshot,name,parent):
    from .calibration import restore_uvs
    cmds,om,_=geo.modules();node=cmds.createNode('transform',name=name,parent=parent)
    node=cmds.ls(node,long=True)[0]
    cmds.xform(node,worldSpace=True,matrix=[1.,0,0,0,0,1.,0,0,0,0,1.,0,0,0,0,1.])
    selected=om.MSelectionList();selected.add(node);fn=om.MFnMesh()
    fn.create([om.MPoint(*p) for p in snapshot['points']],snapshot['face_counts'],snapshot['face_indices'],parent=selected.getDependNode(0))
    restore_uvs(fn,snapshot)
    if snapshot.get('normal_state'):
        restore_state(fn,snapshot['normal_state'],snapshot['face_counts'],snapshot['face_indices'])
    shaders=defaultdict(list)
    for face,uid in enumerate(snapshot['materials']):shaders[uid].append(f'{node}.f[{face}]')
    for uid,faces in shaders.items():cmds.sets(faces,edit=True,forceElement=cmds.ls(uid)[0])
    cmds.setAttr(fn.fullPathName()+'.displaySmoothMesh',0)
    geo.verify_clean(snapshot,geo.snapshot(node))
    return node
