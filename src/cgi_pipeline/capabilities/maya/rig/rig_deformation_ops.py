"""Canonical blendShape and generic deformer operations for Maya."""

from __future__ import annotations

from typing import Any

from cgi_pipeline.capabilities.maya.rig.rig_ops_common import (
    assert_close,
    cmds_from,
    finite,
    integer,
    leaf_name,
    long_path,
    mesh as _mesh,
    receipt,
    started,
    undo_mutation,
)


_DEFORMER_TYPES = {"cluster", "lattice", "bend", "twist", "flare", "sine", "squash", "wave"}
_DEFORMER_NODE_TYPES = {"cluster", "ffd", "nonLinear"}


def _blendshape(cmds, node: str) -> str:
    path = long_path(cmds, node)
    if cmds.nodeType(path) != "blendShape":
        raise ValueError(f"Node is not a blendShape: {path}")
    return path


def _blend_base(cmds, node: str) -> tuple[str, str]:
    geometries = cmds.blendShape(node, query=True, geometry=True) or []
    if len(geometries) != 1:
        raise ValueError(f"blendShape must deform exactly one base mesh: {node}")
    return _mesh(cmds, geometries[0])


def _validate_topology(cmds, base_shape: str, target_shape: str) -> None:
    base_count = int(cmds.polyEvaluate(base_shape, vertex=True))
    target_count = int(cmds.polyEvaluate(target_shape, vertex=True))
    if base_count != target_count:
        raise ValueError(
            f"BlendShape topology mismatch: base has {base_count} vertices, target has {target_count}"
        )


def _blendshape_create(cmds, params: dict[str, Any]) -> dict[str, Any]:
    base, base_shape = _mesh(cmds, params["base_mesh"])
    targets = params.get("target_meshes") or []
    if not isinstance(targets, list) or len(targets) > 128:
        raise ValueError("target_meshes must be an array with at most 128 items")
    resolved = []
    for target in targets:
        transform, shape = _mesh(cmds, target)
        if transform == base or transform in resolved:
            raise ValueError("BlendShape targets must be unique and different from the base mesh")
        _validate_topology(cmds, base_shape, shape)
        resolved.append(transform)
    origin = params.get("origin", "local")
    if origin not in {"local", "world"}:
        raise ValueError("origin must be local or world")
    kwargs = {"origin": origin}
    if params.get("name"):
        kwargs["name"] = leaf_name(params["name"], "name")

    def create():
        result = cmds.blendShape(*(resolved + [base]), **kwargs) or []
        if len(result) != 1:
            raise RuntimeError("blendShape did not return exactly one deformer")
        return long_path(cmds, result[0])

    def verify(node):
        if cmds.nodeType(node) != "blendShape":
            raise RuntimeError(f"Created node is not a blendShape: {node}")
        actual_base, _shape = _blend_base(cmds, node)
        if actual_base != base:
            raise RuntimeError(f"blendShape base readback mismatch: expected {base}, got {actual_base}")

    node = undo_mutation(cmds, "maya.rig.blendshape.manage", create, verify)
    return {"action": "create", "blend_shape": node, "base_mesh": base,
            "targets": resolved, "target_count": len(resolved), "origin": origin}


def _blendshape_target(cmds, params: dict[str, Any], *, remove: bool) -> dict[str, Any]:
    node = _blendshape(cmds, params["blend_shape"])
    base, base_shape = _blend_base(cmds, node)
    target, target_shape = _mesh(cmds, params["target_mesh"])
    _validate_topology(cmds, base_shape, target_shape)
    raw_index = params.get("index")
    if raw_index is None:
        if remove:
            raise ValueError("remove_target requires an explicit target index")
        raw_index = int(cmds.blendShape(node, query=True, weightCount=True) or 0)
    index = integer(raw_index, "index", minimum=0, maximum=9999)
    full_weight = finite(params.get("full_weight", 1.0), "full_weight", minimum=0.001)
    initial = finite(params.get("weight", 0.0), "weight", minimum=0.0, maximum=1.0)

    def mutate():
        kwargs = {"edit": True, "target": (base, index, target, full_weight)}
        if remove:
            kwargs["remove"] = True
        cmds.blendShape(node, **kwargs)
        if not remove:
            cmds.setAttr(f"{node}.weight[{index}]", initial)
        return None

    def verify(_result):
        indices = cmds.getAttr(f"{node}.weight", multiIndices=True) or []
        if remove and index in indices:
            raise RuntimeError(f"BlendShape target index still exists after removal: {index}")
        if not remove:
            if index not in indices:
                raise RuntimeError(f"BlendShape target index was not created: {index}")
            assert_close(initial, cmds.getAttr(f"{node}.weight[{index}]"), "blendShape weight")

    undo_mutation(cmds, "maya.rig.blendshape.manage", mutate, verify)
    return {"action": "remove_target" if remove else "add_target", "blend_shape": node,
            "base_mesh": base, "target_mesh": target, "index": index,
            "full_weight": full_weight, "weight": initial if not remove else None}


def _blendshape_set_weight(cmds, params: dict[str, Any]) -> dict[str, Any]:
    node = _blendshape(cmds, params["blend_shape"])
    index = integer(params["index"], "index", minimum=0, maximum=9999)
    value = finite(params["weight"], "weight", minimum=0.0, maximum=1.0)
    indices = cmds.getAttr(f"{node}.weight", multiIndices=True) or []
    if index not in indices:
        raise ValueError(f"BlendShape target index does not exist: {index}")
    before = float(cmds.getAttr(f"{node}.weight[{index}]"))

    def mutate():
        cmds.setAttr(f"{node}.weight[{index}]", value)

    def verify(_result):
        assert_close(value, cmds.getAttr(f"{node}.weight[{index}]"), "blendShape weight")

    undo_mutation(cmds, "maya.rig.blendshape.manage", mutate, verify)
    return {"action": "set_weight", "blend_shape": node, "index": index,
            "before": before, "after": value}


def manage_blendshape(params: dict, context) -> dict:
    began = started()
    cmds = cmds_from(context)

    def run():
        action = params.get("action")
        if action == "create":
            return _blendshape_create(cmds, params)
        if action == "add_target":
            return _blendshape_target(cmds, params, remove=False)
        if action == "remove_target":
            return _blendshape_target(cmds, params, remove=True)
        if action == "set_weight":
            return _blendshape_set_weight(cmds, params)
        raise ValueError("action must be create, add_target, remove_target, or set_weight")

    return receipt(
        context, "maya.rig.blendshape.manage", began, params,
        "MAYA_RIG_BLENDSHAPE_MANAGE_FAILED",
        "Use compatible mesh topology, an existing blendShape, and a valid target index.", run,
    )


def _deformer(cmds, node: str) -> str:
    path = long_path(cmds, node)
    if cmds.nodeType(path) not in _DEFORMER_NODE_TYPES:
        raise ValueError(f"Unsupported deformer node type: {cmds.nodeType(path)}")
    return path


def manage_deformers(params: dict, context) -> dict:
    began = started()
    cmds = cmds_from(context)

    def run():
        action = params.get("action")
        if action == "create":
            geometry = long_path(cmds, params["geometry"])
            deformer_type = params.get("deformer_type", "cluster")
            if deformer_type not in _DEFORMER_TYPES:
                raise ValueError(
                    "deformer_type must be cluster, lattice, bend, twist, flare, sine, squash, or wave; "
                    "use maya.rig.blendshape.manage for blendShape"
                )
            kwargs = {}
            if params.get("name"):
                kwargs["name"] = leaf_name(params["name"], "name")

            def create():
                if deformer_type == "cluster":
                    result = cmds.cluster(geometry, **kwargs) or []
                elif deformer_type == "lattice":
                    result = cmds.lattice(geometry, **kwargs) or []
                else:
                    result = cmds.nonLinear(geometry, type=deformer_type, **kwargs) or []
                if not result:
                    raise RuntimeError(f"{deformer_type} command returned no nodes")
                return [long_path(cmds, node) for node in result]

            def verify(nodes):
                if cmds.nodeType(nodes[0]) not in _DEFORMER_NODE_TYPES:
                    raise RuntimeError(f"Unexpected deformer node type: {cmds.nodeType(nodes[0])}")

            nodes = undo_mutation(cmds, "maya.rig.deformers.manage", create, verify)
            return {"action": action, "geometry": geometry, "deformer_type": deformer_type,
                    "deformer": nodes[0], "auxiliary_nodes": nodes[1:]}
        if action == "set_envelope":
            node = _deformer(cmds, params["deformer"])
            value = finite(params["envelope"], "envelope", minimum=0.0, maximum=1.0)
            before = float(cmds.getAttr(f"{node}.envelope"))

            def mutate():
                cmds.setAttr(f"{node}.envelope", value)

            def verify(_result):
                assert_close(value, cmds.getAttr(f"{node}.envelope"), "deformer envelope")

            undo_mutation(cmds, "maya.rig.deformers.manage", mutate, verify)
            return {"action": action, "deformer": node, "before": before, "after": value}
        if action == "delete":
            node = _deformer(cmds, params["deformer"])

            def mutate():
                cmds.delete(node)

            def verify(_result):
                if cmds.ls(node, long=True) or []:
                    raise RuntimeError(f"Deformer still exists after deletion: {node}")

            undo_mutation(cmds, "maya.rig.deformers.manage", mutate, verify)
            return {"action": action, "deleted": node}
        raise ValueError("action must be create, set_envelope, or delete")

    return receipt(
        context, "maya.rig.deformers.manage", began, params,
        "MAYA_RIG_DEFORMERS_MANAGE_FAILED",
        "Use an existing geometry/deformer and one explicitly supported native deformer type.", run,
    )
