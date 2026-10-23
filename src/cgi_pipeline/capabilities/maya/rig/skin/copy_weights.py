"""Copy or mirror Maya skin weights with bounded native readback validation."""

from __future__ import annotations

import math
from typing import Any

from cgi_pipeline.capabilities.maya.rig.rig_ops_common import (
    cmds_from,
    leaf_name,
    long_path,
    mesh as _mesh,
    receipt,
    started,
    undo_mutation,
)


_SURFACE_ASSOCIATIONS = {"closestPoint", "rayCast", "closestComponent"}
_INFLUENCE_ASSOCIATIONS = {"closestJoint", "closestBone", "oneToOne", "label", "name"}
_MIRROR_MODES = {"XY", "YZ", "XZ"}


def _skin(cmds, shape: str, explicit: str = "", *, required: bool = True) -> str:
    history = cmds.ls(cmds.listHistory(shape, pruneDagObjects=True) or [], long=True) or []
    if explicit:
        node = long_path(cmds, explicit)
        if cmds.nodeType(node) != "skinCluster" or node not in history:
            raise ValueError(f"skinCluster does not deform the requested mesh: {node}")
        return node
    clusters = cmds.ls(history, long=True, type="skinCluster") or []
    if not clusters and not required:
        return ""
    if len(clusters) != 1:
        raise ValueError(f"Expected exactly one skinCluster on mesh shape: {shape}")
    return str(clusters[0])


def _sample_indices(vertex_count: int) -> list[int]:
    if vertex_count < 1:
        raise ValueError("Target mesh must contain at least one vertex")
    return sorted({0, vertex_count // 2, vertex_count - 1})


def copy_weights(params: dict, context) -> dict:
    began = started()
    cmds = cmds_from(context)

    def run():
        if params.get("binding_policy", "copy_weights") == "inherit_source":
            from .inherit_binding import run
            return run(params, cmds)
        source, source_shape = _mesh(cmds, params["source_mesh"])
        target, target_shape = _mesh(cmds, params["target_mesh"])
        if source == target:
            raise ValueError("source_mesh and target_mesh must be different")
        source_skin = _skin(cmds, source_shape, params.get("source_skin_cluster") or "")
        target_skin = _skin(
            cmds, target_shape, params.get("target_skin_cluster") or "", required=False
        )
        create_missing = params.get("create_missing_target_skin_cluster", False)
        mirror = params.get("mirror", False)
        normalize = params.get("normalize", True)
        if not all(isinstance(value, bool) for value in (create_missing, mirror, normalize)):
            raise ValueError("create_missing_target_skin_cluster, mirror, and normalize must be boolean")
        if not target_skin and not create_missing:
            raise ValueError("Target mesh has no skinCluster; enable create_missing_target_skin_cluster")
        surface = params.get("surface_association", "closestPoint")
        if surface not in _SURFACE_ASSOCIATIONS:
            raise ValueError("surface_association must be closestPoint, rayCast, or closestComponent")
        associations = params.get("influence_association") or ["closestJoint", "oneToOne", "name"]
        if not isinstance(associations, list) or not 1 <= len(associations) <= 3:
            raise ValueError("influence_association must contain 1 to 3 strategies")
        if len(set(associations)) != len(associations) or not set(associations) <= _INFLUENCE_ASSOCIATIONS:
            raise ValueError("influence_association contains duplicate or unsupported strategies")
        mirror_mode = params.get("mirror_mode", "YZ")
        if mirror_mode not in _MIRROR_MODES:
            raise ValueError("mirror_mode must be XY, YZ, or XZ")
        source_influences = [
            long_path(cmds, node)
            for node in (cmds.skinCluster(source_skin, query=True, influence=True) or [])
        ]
        if not 1 <= len(source_influences) <= 256:
            raise ValueError("Source skinCluster must contain 1 to 256 influences")
        vertex_count = int(cmds.polyEvaluate(target_shape, vertex=True))
        samples = _sample_indices(vertex_count)
        target_name = params.get("target_skin_name") or ""
        if target_name:
            target_name = leaf_name(target_name, "target_skin_name")

        state: dict[str, Any] = {"target_skin": target_skin, "created": False}

        def mutate():
            if not state["target_skin"]:
                kwargs = {"toSelectedBones": True}
                if target_name:
                    kwargs["name"] = target_name
                result = cmds.skinCluster(*(source_influences + [target]), **kwargs) or []
                if len(result) != 1:
                    raise RuntimeError("skinCluster did not return exactly one target binding")
                state["target_skin"] = long_path(cmds, result[0])
                state["created"] = True
            copy_options = {
                "sourceSkin": source_skin,
                "destinationSkin": state["target_skin"],
                "noMirror": not mirror,
                "surfaceAssociation": surface,
                "influenceAssociation": list(associations),
                "normalize": normalize,
            }
            if mirror:
                copy_options["mirrorMode"] = mirror_mode
            cmds.copySkinWeights(**copy_options)
            return state["target_skin"]

        readback = []

        def verify(node):
            actual = _skin(cmds, target_shape, node)
            target_influences = cmds.skinCluster(actual, query=True, influence=True) or []
            if not target_influences:
                raise RuntimeError("Target skinCluster has no influences after copy")
            for index in samples:
                values = [float(value) for value in (
                    cmds.skinPercent(actual, f"{target_shape}.vtx[{index}]", query=True, value=True) or []
                )]
                if not values or any(not math.isfinite(value) or value < 0 for value in values):
                    raise RuntimeError(f"Invalid copied skin weights on vertex {index}")
                total = sum(values)
                if normalize and not math.isclose(total, 1.0, rel_tol=1e-5, abs_tol=1e-5):
                    raise RuntimeError(f"Copied weights are not normalized on vertex {index}: {total}")
                readback.append({"vertex": index, "total_weight": total,
                                 "influence_count": len(values)})

        target_skin = undo_mutation(cmds, "maya.rig.skin.copy_weights", mutate, verify)
        return {"source_mesh": source, "target_mesh": target,
                "source_skin_cluster": source_skin, "target_skin_cluster": target_skin,
                "created_target_skin_cluster": state["created"],
                "surface_association": surface, "influence_association": list(associations),
                "mirror": mirror, "mirror_mode": mirror_mode, "normalize": normalize,
                "verified_vertices": readback}

    return receipt(
        context, "maya.rig.skin.copy_weights", began, params,
        "MAYA_RIG_SKIN_COPY_WEIGHTS_FAILED",
        "Use two valid meshes, an explicit source skinCluster, and supported association strategies.", run,
    )
