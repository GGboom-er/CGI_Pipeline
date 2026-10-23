"""Explicit face partitions, native Reduce, and source-vertex correspondence.

Region decisions are supplied by the caller. No character names or automatic
deletion of original meshes live here. New mesh creation is owned by the caller's
native undo transaction; API writes only touch those newly-created meshes.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

from . import geometry as geo


def validate(regions, face_count):
    if not isinstance(regions, list) or not regions:
        raise ValueError('regions must be a nonempty explicit face partition')
    seen = set()
    names = set()
    for region in regions:
        faces = region.get('faces')
        ratio = region.get('keep_face_ratio')
        name = region.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) or name in names:
            raise ValueError('Region names must be unique ASCII identifiers')
        if not isinstance(faces, list) or not faces or any(type(f) is not int or not 0 <= f < face_count for f in faces):
            raise ValueError('Region requires valid source face IDs')
        if len(set(faces)) != len(faces) or seen.intersection(faces):
            raise ValueError('Regions overlap or repeat faces')
        if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 < ratio <= 1:
            raise ValueError('Region keep_face_ratio must be in (0,1]')
        if region.get('search') is not None:
            from .search import validate as validate_search
            options=validate_search(region['search'])
            groups=options.get('work_groups',[])
            if groups and {f for group in groups for f in group['faces']}!=set(faces):
                raise ValueError('Visual work groups must cover the enclosing region')
        seen.update(faces)
        names.add(name)
    if seen != set(range(face_count)):
        raise ValueError('Regions must cover every source face exactly once')


def face_vertices(mesh):
    counts, indices = mesh.getVertices()
    result, offset = [], 0
    for count in counts:
        result.append(list(indices[offset:offset + count]))
        offset += count
    return result


def expand_faces(faces, polygons, rings=1):
    result = set(faces)
    for _ in range(rings):
        vertices = {v for f in result for v in polygons[f]}
        result.update(i for i, ids in enumerate(polygons) if vertices.intersection(ids))
    return sorted(result)


def shells(polygons):
    """Linear union-find over topology; return stable face-shell IDs."""
    parents = list(range(len(polygons)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    owner = {}
    for face, vertices in enumerate(polygons):
        for vertex in vertices:
            if vertex in owner:
                a, b = root(face), root(owner[vertex])
                parents[max(a, b)] = min(a, b)
            else:
                owner[vertex] = face
    groups = defaultdict(list)
    for face in range(len(polygons)):
        groups[root(face)].append(face)
    return list(groups.values())


def capture(source, *, points=None):
    """Read immutable source construction data once per operation, not per group."""
    cmds, om, _ = geo.modules()
    shape = cmds.listRelatives(source, s=True, ni=True, f=True, type='mesh')[0]
    dag, mesh = geo.mesh_fn(shape)
    polygons = face_vertices(mesh)
    normals=mesh.getNormals();counts,ids=mesh.getNormalIds();normal_rows=[];offset=0
    for count in counts:
        normal_rows.append(list(ids[offset:offset+count]));offset+=count
    uvs={}
    for uvset in mesh.getUVSetNames():
        u,v=mesh.getUVs(uvset);counts,ids=mesh.getAssignedUVs(uvset);rows=[];offset=0
        for count in counts:
            rows.append(list(ids[offset:offset+count]));offset+=count
        uvs[uvset]=(u,v,counts,rows)
    shaders,assignment=mesh.getConnectedShaders(dag.instanceNumber())
    if any(i<0 for i in assignment):raise ValueError('Source face has no material')
    from . import constraints,mesh_io
    triangle_counts,triangles=mesh.getTriangles()
    return {'source':source,'polygons':polygons,'points':points if points is not None else mesh.getPoints(),
            'points_override':points is not None,
            'matrix':cmds.xform(source,q=True,ws=True,matrix=True),
            'parent':(cmds.listRelatives(source,p=True,f=True) or ['|'])[0],
            'normals':normals,'normal_rows':normal_rows,'uvs':uvs,'shaders':shaders,
            'assignment':assignment,'uv_sensitive':constraints.capture(shaders),
            'triangle_counts':triangle_counts,'triangles':triangles,
            'normal_state':mesh_io.normal_state(mesh,om.MSpace.kObject)}


def corner_mapping(polygons, faces):
    """Split cut vertex fans, including bow-ties inside one edge-connected patch."""
    original=[v for f in faces for v in polygons[f]]
    parents=list(range(len(original)));edges={};offset=0
    def root(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    for f in faces:
        row=polygons[f]
        for index,(a,b) in enumerate(zip(row,row[1:]+row[:1])):
            edge=tuple(sorted((a,b)))
            pair={a:offset+index,b:offset+(index+1)%len(row)}
            if edge in edges:
                for vertex in edge:
                    x,y=root(pair[vertex]),root(edges[edge][vertex]);parents[max(x,y)]=min(x,y)
            else:edges[edge]=pair
        offset+=len(row)
    keys=sorted({(v,root(i)) for i,v in enumerate(original)})
    lookup={key:i for i,key in enumerate(keys)}
    return [v for v,_ in keys],[lookup[v,root(i)] for i,v in enumerate(original)]


def extract(source, faces, name, *, points=None, source_cache=None):
    """Copy selected source faces with explicit original vertex IDs and UVs."""
    cmds, om, _ = geo.modules()
    cache=source_cache if source_cache is not None else capture(source,points=points)
    if cache['source']!=source:raise ValueError('Source cache belongs to a different mesh')
    polygons=cache['polygons']
    faces = sorted(faces)
    source_ids,corners=corner_mapping(polygons,faces)
    if faces == list(range(len(polygons))) and source_ids == list(range(len(cache['points']))):
        # Whole connected surfaces need no topology/UV/normal reconstruction.
        from . import ops,mesh_io
        transform=ops._copy(source,name)
        if cache.get('points_override'):
            _,fn=geo.mesh_fn(ops._shape(transform))
            fn.setPoints(cache['points'])
            counts,indices=fn.getVertices()
            mesh_io.restore_state(fn,cache['normal_state'],counts,indices)
        return transform,source_ids
    transform = cmds.createNode('transform', name=name,
                                parent=cache['parent'])
    transform = cmds.ls(transform, l=True)[0]
    cmds.xform(transform, ws=True, matrix=cache['matrix'])
    selection = om.MSelectionList(); selection.add(transform)
    original = cache['points']
    fn = om.MFnMesh()
    fn.create([original[v] for v in source_ids], [len(polygons[f]) for f in faces],
              corners, parent=selection.getDependNode(0))
    new_shape = om.MFnDagNode(fn.object()).fullPathName()
    from . import mesh_io
    offsets=[0]
    for row in polygons:offsets.append(offsets[-1]+len(row))
    state={key:[value[i] for f in faces for i in range(offsets[f],offsets[f+1])]
           for key,value in cache['normal_state'].items()}
    mesh_io.restore_state(fn,state,[len(polygons[f]) for f in faces],corners)
    for uvset,(u,v,counts,rows) in cache['uvs'].items():
        if uvset not in fn.getUVSetNames():
            fn.createUVSet(uvset)
        fn.setUVs(u, v, uvset)
        fn.assignUVs([counts[f] for f in faces], [i for f in faces for i in rows[f]], uvset)
    shaders, assignment = cache['shaders'],cache['assignment']
    groups = defaultdict(list)
    for f, old in enumerate(faces):
        if assignment[old] < 0:
            raise ValueError('Source face has no material')
        groups[assignment[old]].append(f'{transform}.f[{f}]')
    for shader, components in groups.items():
        cmds.sets(components, e=True, forceElement=om.MFnDependencyNode(shaders[shader]).name())
    return transform, source_ids


def reduce_region(node, source_ids, ratio, *, preserve_border=False, reduce_flags=None):
    from .ops import REDUCE, _copy, _shape
    cmds, om, _ = geo.modules()
    before = int(cmds.polyEvaluate(node, face=True))
    if ratio == 1:
        return node, source_ids, {'before_faces': before, 'after_faces': before}
    map_name = '__cgi_source_vertex'
    cmds.polyReduce(node, percentage=100*(1-ratio), vertexMapName=map_name,
                    **{**REDUCE, **(reduce_flags or {}), 'keepBorderWeight': 1.0 if preserve_border else .5})
    clean = _copy(node, node.rsplit('|', 1)[-1] + '_clean')
    _, fn = geo.mesh_fn(_shape(clean))
    colors = fn.getVertexColors(map_name)
    mapping = [int(round(c.r))*65536 + int(round(c.g)) for c in colors]
    if any(i < 0 or i >= len(source_ids) for i in mapping):
        raise RuntimeError('Native Reduce vertex map is invalid')
    cmds.polyColorSet(clean, delete=True, colorSet=map_name)
    cmds.delete(node)
    return clean, [source_ids[i] for i in mapping], {
        'before_faces': before, 'after_faces': fn.numPolygons}


def construct(source, regions, prefix, *, points=None,workdir=None,normal_state=None):
    """Extract all regions before any topology edit, then reduce each once."""
    cmds, _, _ = geo.modules()
    count = int(cmds.polyEvaluate(source, face=True))
    validate(regions, count)
    cache=capture(source,points=points)
    if normal_state is not None:cache['normal_state']=normal_state
    created = []
    for region in regions:
        if region.get('search') is not None and region['keep_face_ratio']<1:
            from .adaptive import reduce_partition
            row=reduce_partition(source,region,workdir,prefix+'_'+region['name'],cache)
            created.append({**row,'region':region['name']})
            continue
        node, mapping = extract(source, region['faces'], prefix + '_' + region['name'], source_cache=cache)
        node, mapping, counts = reduce_region(node, mapping, region['keep_face_ratio'],
                                             preserve_border=region.get('preserve_border', False))
        created.append({'node': node, 'source_vertex_ids': mapping, 'region': region['name'], **counts})
    return created
