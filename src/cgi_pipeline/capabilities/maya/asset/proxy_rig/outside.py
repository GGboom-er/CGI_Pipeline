"""Lightweight outside-cache dependency audit. Visibility never proves redundancy."""
from __future__ import annotations

import re

from . import deletion_scope


def script_sources(cmds):
    result = []
    for node in cmds.ls(type="expression") or []:
        result.append((node, cmds.expression(node, query=True, string=True) or ""))
    for node in cmds.ls(type="script") or []:
        text = []
        for attr in ("before", "after"):
            if cmds.objExists(node + "." + attr):
                text.append(cmds.getAttr(node + "." + attr) or "")
        result.append((node, "\n".join(text)))
    return result


def name_users(names, scripts):
    users = []
    aliases = {name.rsplit("|", 1)[-1] for name in names}
    for node, text in scripts:
        if any(re.search(r"(?<![\w:])" + re.escape(name) + r"(?![\w:])", text) for name in aliases):
            users.append(node)
    return sorted(set(users))


def audit(cmds, transform, shapes, scripts):
    reasons, consumers = [], []
    history = sorted(set(cmds.listHistory(transform, pruneDagObjects=True) or []))
    deformers = [node for node in history if "geometryFilter" in (cmds.nodeType(node, inherited=True) or [])]
    own_skins = {node for node in deformers if cmds.nodeType(node) == "skinCluster"}
    if any(cmds.nodeType(node) not in ("skinCluster", "tweak") for node in deformers):
        reasons.append("active_non_skin_deformation")
    for skin in own_skins:
        if len(cmds.skinCluster(skin, query=True, geometry=True) or []) != 1:
            reasons.append("shared_skin_output_dependency")
    if len(shapes) != 1 or len(cmds.listRelatives(transform, allParents=True, fullPath=True) or []) > 1:
        reasons.append("instanced_or_multiple_output_shapes")
    if cmds.referenceQuery(transform, isNodeReferenced=True):
        reasons.append("referenced_node")
    if (cmds.lockNode(transform, query=True, lock=True) or [False])[0]:
        reasons.append("locked_node")
    if cmds.listRelatives(transform, children=True, type="transform") or []:
        reasons.append("non_leaf_transform")
    deletion = deletion_scope.inspect(cmds, transform, shapes, history)
    if deletion["external_consumers"]:
        reasons.append("child_shape_external_consumer")
        consumers.extend(deletion["external_consumers"])
    for node in [transform] + shapes:
        outgoing = cmds.listConnections(node, source=False, destination=True) or []
        active = []
        for target in outgoing:
            kind = cmds.nodeType(target)
            if kind == "shadingEngine" or target in own_skins:
                continue
            if kind == "objectSet" and not (set(cmds.listConnections(target, type="geometryFilter") or []) - own_skins):
                continue  # Ordinary selection membership is removed with an explicitly discarded model.
            active.append(target)
        consumers.extend(active)
        if active:
            reasons.append("effective_outgoing_or_message_connection")
        if cmds.objExists(node + ".message"):
            message_users = set(cmds.listConnections(node + ".message", source=False, destination=True) or []) - own_skins
            consumers.extend(message_users)
            if message_users:
                reasons.append("effective_message_connection")
    drivers, evaluation_drivers = [], []
    for node in [transform] + shapes:
        for attr in ("visibility", "lodVisibility", "drawOverride", "translate", "rotate", "scale", "offsetParentMatrix"):
            if cmds.objExists(node + "." + attr):
                incoming = cmds.listConnections(node + "." + attr, source=True, destination=False, plugs=True) or []
                drivers.extend(incoming)
                if attr in ('translate', 'rotate', 'scale', 'offsetParentMatrix'):
                    evaluation_drivers.extend(incoming)
    if evaluation_drivers:
        reasons.append("transform_attachment_or_constraint")
    ancestor_drivers = []
    ancestor = (cmds.listRelatives(transform, parent=True, fullPath=True) or [""])[0]
    while ancestor:
        for attr in ("visibility", "lodVisibility", "drawOverride"):
            if cmds.objExists(ancestor + "." + attr):
                ancestor_drivers.extend(cmds.listConnections(ancestor + "." + attr, source=True,
                                                           destination=False, plugs=True) or [])
        ancestor = (cmds.listRelatives(ancestor, parent=True, fullPath=True) or [""])[0]
    # Incoming display switching only controls this dispensable output. It is
    # recorded, not mistaken for a downstream rig consumer. Attachments,
    # deformation consumers and script/name dependencies still protect it.
    users = name_users([transform] + [item["path"] for item in deletion["shapes"]], scripts)
    if users:
        reasons.append("expression_or_script_name_dependency")
    return {"uuid": (cmds.ls(transform, uuid=True) or [""])[0], "path": transform,
            "shapes": [{"path": shape, "uuid": (cmds.ls(shape, uuid=True) or [""])[0]} for shape in shapes],
            "status": "protected" if reasons else "candidate",
            "protected_reasons": sorted(set(reasons)), "consumers": sorted(set(consumers)),
            "drivers": sorted(set(drivers)), "script_users": users,
            "ancestor_drivers": sorted(set(ancestor_drivers)), "deletion_shapes": deletion["shapes"],
            "self_skin_nodes": sorted(own_skins),
            "evidence": {"scope": "outside_configured_cache", "dependency_audit": "direct graph and explicit script names",
                         "hidden_is_unused": False, "visual_semantics_require_agent_decision": True}}


def inspect(cmds, cache_root):
    by_transform = {}
    for shape in cmds.ls(type="mesh", long=True) or []:
        if cmds.getAttr(shape + ".intermediateObject") or shape.startswith(cache_root + "|"):
            continue
        transform = (cmds.listRelatives(shape, parent=True, fullPath=True) or [""])[0]
        by_transform.setdefault(transform, []).append(shape)
    scripts = script_sources(cmds)
    return [audit(cmds, transform, sorted(shapes), scripts) for transform, shapes in sorted(by_transform.items())]


def preflight_discard(cmds, entries, indexed):
    by_id = {row["uuid"]: row for row in indexed}
    scripts = script_sources(cmds)
    for entry in entries:
        old = by_id[entry["uuid"]]
        shapes = cmds.listRelatives(old["path"], shapes=True, noIntermediate=True,
                                   fullPath=True, type="mesh") or []
        current = audit(cmds, old["path"], shapes, scripts)
        expected_shapes = old.get("shapes") or [{"path": old["shape"], "uuid": old["shape_uuid"]}]
        if current["uuid"] != old["uuid"] or current["shapes"] != expected_shapes:
            raise ValueError("Outside-cache discard identity changed: " + old["path"])
        if old.get("deletion_shapes") and old["deletion_shapes"] != current["deletion_shapes"]:
            raise ValueError("Discard child mesh identity changed: " + old["path"])
        if current["status"] != "candidate":
            raise ValueError("Outside-cache model has an effective rig/proxy dependency: " + str(current["protected_reasons"]))
