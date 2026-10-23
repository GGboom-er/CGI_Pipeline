"""Guard Autodesk MLdeleteUnused; never replace it with another graph cleaner."""
from . import outside


def _nodes_of_class(cmds, classification):
    types = cmds.listNodeTypes(classification) or []
    return set(cmds.ls(type=types, long=True) or []) if types else set()


def preflight(enabled):
    if not isinstance(enabled, bool):
        raise ValueError("cleanup_unused_materials must be boolean")
    if enabled:
        import maya.mel as mel
        if "Unknown" in str(mel.eval('whatIs "MLdeleteUnused"')):
            raise ValueError("The Autodesk MLdeleteUnused MEL entry is unavailable")


def _connections(cmds, nodes):
    result = set()
    for node in sorted(nodes):
        pairs = cmds.listConnections(node, connections=True, plugs=True,
                                     source=True, destination=True) or []
        for index in range(0, len(pairs), 2):
            own, other = pairs[index:index + 2]
            result.add(tuple(sorted((own, other))))
    return result


def _protected_render_graph(cmds, seeds, render):
    """Native DG history queries, never another scene-cleaning traversal."""
    if not seeds:
        return set()
    upstream = set(cmds.listHistory(sorted(seeds), pruneDagObjects=True) or [])
    protected = seeds | (upstream & render)
    downstream = set(cmds.listHistory(sorted(protected), future=True, pruneDagObjects=True) or [])
    protected |= downstream & render
    upstream = set(cmds.listHistory(sorted(protected), pruneDagObjects=True) or [])
    return protected | (upstream & render)


def run(cmds, enabled):
    preflight(enabled)
    if not enabled:
        return {"enabled": False, "scope": "material/texture cleanup explicitly disabled"}
    import maya.mel as mel
    all_nodes = cmds.ls(long=True) or []
    all_identities = set(cmds.ls(uuid=True) or [])
    shaders = set(cmds.ls(materials=True, long=True) or [])
    textures = _nodes_of_class(cmds, "texture")
    shading_groups = set(cmds.ls(type="shadingEngine", long=True) or [])
    utilities = _nodes_of_class(cmds, "utility") | _nodes_of_class(cmds, "math") | _nodes_of_class(cmds, "imageplane")
    render = shaders | textures | shading_groups | utilities
    candidates = shaders | textures | shading_groups
    protected = set(utilities) | (set(cmds.ls(defaultNodes=True, long=True) or []) & candidates)
    bookkeeping = {"defaultShaderList", "defaultTextureList", "defaultRenderUtilityList",
                   "lightLinker", "renderPartition", "materialInfo"}
    scripts = outside.script_sources(cmds)
    for node in sorted(candidates):
        neighbours = cmds.listConnections(node, source=True, destination=True) or []
        if any(other not in render and cmds.nodeType(other) not in bookkeeping for other in neighbours):
            protected.add(node)
        if node in shading_groups and (cmds.sets(node, query=True) or []):
            protected.add(node)  # Native code otherwise also deletes assigned SGs with no shader.
        if outside.name_users([node], scripts):
            protected.add(node)
        if cmds.referenceQuery(node, isNodeReferenced=True):
            protected.add(node)
    protected = _protected_render_graph(cmds, protected, render)
    allowed = candidates - protected
    identities = {node: (cmds.ls(node, uuid=True) or [""])[0] for node in allowed}
    guarded_connections = _connections(cmds, protected & set(all_nodes))
    locks = {}
    try:
        for node in sorted(protected):
            if not cmds.referenceQuery(node, isNodeReferenced=True):
                locks[node] = bool((cmds.lockNode(node, query=True, lock=True) or [False])[0])
                if not locks[node]:
                    cmds.lockNode(node, lock=True)
        mel.eval('source "MLdeleteUnused.mel"')
        native_count = int(mel.eval("MLdeleteUnused()"))
        remaining = set(cmds.ls(uuid=True) or [])
        illegal = sorted((all_identities - remaining) - set(identities.values()))
        if illegal:
            raise RuntimeError("Native material cleanup deleted protected nodes: " + str(illegal[:10]))
        if _connections(cmds, protected & set(all_nodes)) != guarded_connections:
            raise RuntimeError("Native material cleanup changed protected rig/assigned-material connections")
        removed = [{"name": node, "uuid": identities[node]} for node in sorted(allowed)
                   if identities[node] not in remaining]
        return {"enabled": True, "engine": "Autodesk MLdeleteUnused", "native_deleted_count": native_count,
                "deleted": removed, "protected_render_nodes": len(protected),
                "scope": "isolated unused shaders/textures/empty shading groups; utility/math/imagePlane nodes and protected render networks preserved",
                "protected_uuid_guard": True, "critical_connection_guard": True}
    finally:
        restore_errors = []
        for node, locked in locks.items():
            try:
                if cmds.objExists(node):
                    cmds.lockNode(node, lock=locked)
            except RuntimeError as exc:
                restore_errors.append(node + ": " + str(exc))
        if restore_errors:
            raise RuntimeError("Native material cleanup could not restore all editing locks: " + str(restore_errors[:10]))
