"""Preserve whole-object user sets/layers; topology-dependent sets need a decision."""

_ISOLATION_SETS = object()


def _isolation_sets(cmds, cache):
    """One native panel query per inventory cache; names alone prove nothing."""
    if _ISOLATION_SETS not in cache:
        isolated = set()
        if not cmds.about(batch=True):
            for panel in cmds.getPanel(type="modelPanel") or []:
                objects = cmds.isolateSelect(panel, query=True, viewObjects=True)
                if objects:
                    isolated.add(objects)
        cache[_ISOLATION_SETS] = isolated
    return cache[_ISOLATION_SETS]


def inspect(cmds, transform, shape, cache):
    sets, components, memberships = [], [], []
    isolation_sets = _isolation_sets(cmds, cache)
    aliases = {transform, shape, transform.rsplit("|", 1)[-1], shape.rsplit("|", 1)[-1]}
    for name in sorted(cmds.listSets(object=transform, extendToShape=True) or []):
        if name in isolation_sets:
            continue
        if name not in cache:
            is_material = cmds.nodeType(name) == "shadingEngine"
            is_deformer = bool(cmds.listConnections(name, type="geometryFilter") or [])
            cache[name] = None if is_material or is_deformer else (cmds.sets(name, query=True) or [])
        members = cache[name]
        if members is None:
            continue
        related = [member for member in members if member.split(".", 1)[0] in aliases]
        if any("." in member for member in related):
            components.append(name)
        if any("." not in member for member in related):
            sets.append(name)
        for member in related:
            if "." not in member:
                label = "shape" if member in {shape, shape.rsplit("|", 1)[-1]} else "transform"
                memberships.append({"set": name, "target": label})
    layers = {}
    for label, target in (("transform", transform), ("shape", shape)):
        layers[label] = sorted(set(cmds.listConnections(
            target, source=True, destination=True, type="displayLayer") or []) - {"defaultLayer"})
    memberships = sorted({(item["set"], item["target"]) for item in memberships})
    return {"object_sets": sets, "object_set_members": [{"set": name, "target": label}
            for name, label in memberships], "component_sets": components, "display_layers": layers}


def transfer(cmds, original, target):
    shape = (cmds.listRelatives(target, shapes=True, noIntermediate=True,
                                fullPath=True, type="mesh") or [None])[0]
    for item in original.get("memberships", {}).get("object_set_members", []):
        cmds.sets(target if item["target"] == "transform" else shape, addElement=item["set"])
    for label, layers in original.get("memberships", {}).get("display_layers", {}).items():
        for layer in layers:
            cmds.editDisplayLayerMembers(layer, target if label == "transform" else shape, noRecurse=True)


def transfer_vertices(cmds, original, target, original_ids):
    """Map surviving user components by exact original vertex identity.

    Removed edges/faces remain in the original selection set; never substitute
    a nearby component on reduced geometry. Rig/deformer sets are excluded by inspect.
    """
    import re
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce import geometry, regions
    aliases = {original['path'], original['shape'], original['path'].rsplit('|',1)[-1],
               original['shape'].rsplit('|',1)[-1]}
    for name in original.get('memberships', {}).get('component_sets', []):
        related = [m.split('.',1)[1] for m in cmds.sets(name,q=True) or []
                   if '.' in m and m.split('.',1)[0] in aliases]
        selected = {'vtx':set(), 'e':set(), 'f':set()}
        for token in related:
            match=re.fullmatch(r'(vtx|e|f)\[(\d+)(?::(\d+))?\]',token)
            if not match:raise ValueError('Unsupported component selection: '+token)
            selected[match[1]].update(range(int(match[2]),int(match[3] or match[2])+1))
        components = [f'{target}.vtx[{i}]' for i, source in enumerate(original_ids) if source in selected['vtx']]
        if selected['e'] or selected['f']:
            old_fn=geometry.mesh_fn(original['shape'])[1]
            new_shape=cmds.listRelatives(target,s=True,ni=True,f=True,type='mesh')[0]
            new_fn=geometry.mesh_fn(new_shape)[1]
            edges={tuple(sorted(old_fn.getEdgeVertices(i))) for i in selected['e']}
            if edges:
                components.extend(f'{target}.e[{i}]' for i in range(new_fn.numEdges)
                    if tuple(sorted(original_ids[v] for v in new_fn.getEdgeVertices(i))) in edges)
            if selected['f']:
                old_faces=regions.face_vertices(old_fn)
                faces={tuple(sorted(old_faces[i])) for i in selected['f']}
                components.extend(f'{target}.f[{i}]' for i,verts in enumerate(regions.face_vertices(new_fn))
                    if tuple(sorted(original_ids[v] for v in verts)) in faces)
        if components:
            cmds.sets(components, addElement=name)
            if not cmds.sets(components, isMember=name):
                raise RuntimeError('Mapped component membership readback failed: ' + name)
