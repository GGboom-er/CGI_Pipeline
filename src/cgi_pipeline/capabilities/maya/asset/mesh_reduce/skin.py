"""Native undoable binding transfer; OpenMaya is used only for bulk reads."""

from __future__ import annotations

import math

from .geometry import flat, mesh_fn, modules


SETTINGS = ("skinningMethod", "normalizeWeights", "maxInfluences", "maintainMaxInfluences",
            "envelope", "dqsSupportNonRigid", "dqsScale", "weightDistribution")


def skin_fn(skin):
    _cmds, om, oma = modules()
    selected = om.MSelectionList()
    selected.add(skin)
    return oma.MFnSkinCluster(selected.getDependNode(0))


def capture_weights(skin, shape):
    cmds, om, _oma = modules()
    dag, mesh = mesh_fn(shape)
    fn = skin_fn(skin)
    components = om.MFnSingleIndexedComponent()
    component = components.create(om.MFn.kMeshVertComponent)
    components.addElements(range(mesh.numVertices))
    values, width = fn.getWeights(dag, component)
    influences = fn.influenceObjects()
    keys = [cmds.ls(path.fullPathName(), uuid=True)[0] for path in influences]
    if width != len(keys) or len(values) != mesh.numVertices * width:
        raise ValueError("Skin weight dimensions are inconsistent: " + skin)
    rows = []
    for vertex in range(mesh.numVertices):
        row = {keys[column]: float(values[vertex * width + column])
               for column in range(width) if values[vertex * width + column] != 0}
        if any(not math.isfinite(value) or value < -1e-12 for value in row.values()):
            raise ValueError("Skin weights must be finite and nonnegative: " + skin)
        # Numerical negatives from native interpolation are zero, not influences.
        row = {key: value for key, value in row.items() if value > 0}
        if not math.isclose(sum(row.values()), 1.0, abs_tol=1e-5):
            raise ValueError("Skin weights must sum to one before geometry transfer: " + skin)
        rows.append(row)
    return {"weights": rows, "blend_weights": list(fn.getBlendWeights(dag, component))}


def binding_context(skin, shape, *, require_rest=True):
    cmds, om, _oma = modules()
    fn = skin_fn(skin)
    indices = cmds.deformer(skin, query=True, geometryIndices=True) or []
    if len(indices) != 1 or len(fn.getOutputGeometry()) != 1:
        raise ValueError("A skinCluster with multiple outputs requires protection: " + skin)
    if cmds.attributeQuery("relativeSpaceMode", node=skin, exists=True):
        if cmds.getAttr(skin + ".relativeSpaceMode") != 0:
            raise ValueError("Non-standard skin relative space requires a dedicated strategy: " + skin)
    if cmds.getAttr(skin + ".nodeState") != 0 or cmds.getAttr(skin + ".envelope") != 1:
        raise ValueError("Source skin must be active with envelope one: " + skin)
    settings = {key: cmds.getAttr(skin + "." + key) for key in SETTINGS
                if cmds.attributeQuery(key, node=skin, exists=True)}
    for key in settings:
        if cmds.listConnections(skin + "." + key, source=True, destination=False):
            raise ValueError("Driven skin settings require protection: " + skin + "." + key)
    selected = om.MSelectionList()
    selected.add(f"{skin}.input[{indices[0]}].inputGeometry")
    input_mesh = om.MFnMesh(selected.getPlug(0).asMObject())
    _dag, output_mesh = mesh_fn(shape)
    if require_rest:
        before = input_mesh.getPoints(om.MSpace.kObject)
        after = output_mesh.getPoints(om.MSpace.kObject)
        if len(before) != len(after) or any(a.distanceTo(b) > 1e-5 for a, b in zip(before, after)):
            raise ValueError("Current output differs from its skin input; restore this mesh's rest pose: " + shape)
    bindings = {}
    influence_names = [path.fullPathName() for path in fn.influenceObjects()]
    if len({name.rsplit("|", 1)[-1] for name in influence_names}) != len(influence_names):
        raise ValueError("Name-based transfer requires unique influence leaf names: " + skin)
    for path in fn.influenceObjects():
        name = path.fullPathName()
        logical = fn.indexForInfluenceObject(path)
        matrix_plug = f"{skin}.matrix[{logical}]"
        expected = name + ".worldMatrix[0]"
        actual_selection = om.MSelectionList()
        actual_selection.add(matrix_plug)
        incoming = actual_selection.getPlug(0).connectedTo(True, False)
        expected_selection = om.MSelectionList()
        expected_selection.add(expected)
        if len(incoming) != 1 or incoming[0] != expected_selection.getPlug(0):
            raise ValueError("Custom skin matrix input requires protection: " + matrix_plug)
        pre_plug = f"{skin}.bindPreMatrix[{logical}]"
        if cmds.listConnections(pre_plug, source=True, destination=False):
            raise ValueError("Driven bindPreMatrix requires protection: " + pre_plug)
        bindings[cmds.ls(name, uuid=True)[0]] = {
            "name": name, "bind_pre": flat(cmds.getAttr(pre_plug)), "matrix_source": expected}
    return {"skin": skin, "settings": settings, "bindings": bindings,
            "geom_matrix": flat(cmds.getAttr(skin + ".geomMatrix"))}


def _set_setting(cmds, plug, value):
    if isinstance(value, (tuple, list)):
        values = flat(value)
        cmds.setAttr(plug, *values, type="double3")
    else:
        cmds.setAttr(plug, value)


def bind_copy(source_context, target, prepared_weights=None):
    cmds, _om, _oma = modules()
    settings = source_context["settings"]
    bindings = source_context["bindings"]
    names = [row["name"] for row in bindings.values()]
    skin = cmds.skinCluster(names, target, toSelectedBones=True, normalizeWeights=1,
                            skinMethod=int(settings["skinningMethod"]),
                            maximumInfluences=max(1, len(names)), obeyMaxInfluences=False)[0]
    cmds.setAttr(skin + ".maintainMaxInfluences", False)
    if prepared_weights is None:
        cmds.copySkinWeights(sourceSkin=source_context["skin"], destinationSkin=skin,
                             noMirror=True, surfaceAssociation="closestPoint",
                             influenceAssociation="name", noBlendWeight=False, normalize=True)
    else:
        # Only used on this newly-created same-topology skin. Its node creation
        # owns undo; original skins are never assigned through this path.
        import numpy as np
        fn = skin_fn(skin)
        path = _om.MDagPath.getAPathTo(fn.getOutputGeometry()[0])
        count = _om.MFnMesh(path).numVertices
        data = np.asarray(prepared_weights["expected"])
        if data.shape[0] != count:
            raise ValueError("Prepared source weights require identical topology")
        source_ids = [r["uuid"] for r in prepared_weights["influences"]]
        order = [source_ids.index(cmds.ls(p.fullPathName(), uuid=True)[0]) for p in fn.influenceObjects()]
        comp = _om.MFnSingleIndexedComponent()
        component = comp.create(_om.MFn.kMeshVertComponent)
        comp.setCompleteData(count)
        fn.setWeights(path, component, _om.MIntArray(range(len(order))),
                      _om.MDoubleArray(data[:, order].ravel().tolist()), False)
        fn.setBlendWeights(path, component, _om.MDoubleArray(prepared_weights["blend_weights"]))
    restore_binding_context(skin, source_context)
    return skin


def restore_binding_context(skin, source_context):
    """Retain original binding matrices/settings after native copy or merge.

    polyUniteSkinned may rebuild bindPreMatrix from the current joint pose.
    Inputs have already passed compatibility; keep their original bind context.
    """
    cmds, _om, _oma = modules()
    fn = skin_fn(skin)
    bindings = source_context["bindings"]
    paths = list(fn.influenceObjects())
    if {cmds.ls(path.fullPathName(), uuid=True)[0] for path in paths} != set(bindings):
        raise ValueError("Native output influence identities differ from binding union")
    for path in paths:
        key = cmds.ls(path.fullPathName(), uuid=True)[0]
        logical = fn.indexForInfluenceObject(path)
        cmds.setAttr(f"{skin}.bindPreMatrix[{logical}]", *bindings[key]["bind_pre"], type="matrix")
    cmds.setAttr(skin + ".geomMatrix", *source_context["geom_matrix"], type="matrix")
    for name, value in source_context["settings"].items():
        _set_setting(cmds, skin + "." + name, value)


def matrix_equal(left, right):
    """Native matrix recomposition introduces roundoff, not a new bind pose."""
    return len(left) == len(right) and all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9)
                                         for a, b in zip(left, right))


def bindings_equal(left, right):
    return left.keys() == right.keys() and all(
        left[key]["name"] == right[key]["name"] and
        left[key]["matrix_source"] == right[key]["matrix_source"] and
        matrix_equal(left[key]["bind_pre"], right[key]["bind_pre"])
        for key in left)


def compatible(contexts):
    skinned = [bool(item) for item in contexts]
    if len(set(skinned)) != 1:
        raise ValueError("Static and skinned outputs cannot be combined together")
    if not skinned[0]:
        return
    seen = {}
    for context in contexts:
        if context["settings"] != contexts[0]["settings"]:
            raise ValueError("Skin settings differ; preserve separate outputs")
        if not matrix_equal(context["geom_matrix"], contexts[0]["geom_matrix"]):
            raise ValueError("Skin geometry binding matrices differ; preserve separate outputs")
        for key, value in context["bindings"].items():
            if key in seen and not bindings_equal({key: seen[key]}, {key: value}):
                raise ValueError("Shared influences have incompatible bind contexts")
            seen[key] = value


def verify_weights(expected, actual):
    left, right = expected["weights"], actual.get("weights", [])
    values_match = len(left) == len(right) and all(
        all(abs(a.get(key, 0) - b.get(key, 0)) <= 1e-7 for key in set(a) | set(b))
        for a, b in zip(left, right))
    a, b = expected["blend_weights"], actual.get("blend_weights", [])
    return {"weights": values_match,
            "blend_weights": len(a) == len(b) and all(abs(x - y) <= 1e-7 for x, y in zip(a, b))}
