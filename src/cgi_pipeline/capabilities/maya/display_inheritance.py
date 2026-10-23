"""Shared local display inheritance; ancestor controls stay in their original DAG."""
from __future__ import annotations

DISPLAY_ATTRS = ("visibility", "lodVisibility", "overrideEnabled", "overrideVisibility",
                 "overrideDisplayType", "overrideShading", "overrideTexturing",
                 "overrideLevelOfDetail", "overridePlayback", "overrideRGBColors", "overrideColor")


def display_state(cmds, node):
    return {attr: {"value": cmds.getAttr(node + "." + attr), "inputs": sorted(
        cmds.listConnections(node + "." + attr, source=True, destination=False, plugs=True) or [])}
        for attr in DISPLAY_ATTRS if cmds.objExists(node + "." + attr)}


def visibility_context(cmds, transform):
    parent = (cmds.listRelatives(transform, parent=True, fullPath=True) or [""])[0]
    ancestors, cursor = [], parent
    while cursor:
        ancestors.append({"path": cursor, "display": display_state(cmds, cursor)})
        cursor = (cmds.listRelatives(cursor, parent=True, fullPath=True) or [""])[0]
    return {"parent": parent, "local": display_state(cmds, transform), "ancestors": ancestors}


def apply_state(cmds, state, target):
    """Reject conflicting target drivers before writing; preserve target attr locks."""
    for attr, value in state.items():
        plug = target + "." + attr
        if not cmds.objExists(plug):
            raise ValueError("Target lacks display attribute: " + plug)
        existing = sorted(cmds.listConnections(plug, source=True, destination=False, plugs=True) or [])
        if existing and existing != value["inputs"]:
            raise ValueError("Conflicting target display input: " + plug)
    for attr, value in state.items():
        plug = target + "." + attr
        existing = sorted(cmds.listConnections(plug, source=True, destination=False, plugs=True) or [])
        if existing == value["inputs"] and (existing or cmds.getAttr(plug) == value["value"]):
            continue
        locked = cmds.getAttr(plug, lock=True)
        try:
            if locked:
                cmds.setAttr(plug, lock=False)
            if value["inputs"]:
                cmds.connectAttr(value["inputs"][0], plug)
            else:
                cmds.setAttr(plug, value["value"])
        finally:
            if locked:
                cmds.setAttr(plug, lock=True)
    if display_state(cmds, target) != state:
        raise RuntimeError("Display inheritance readback failed: " + target)


def copy_display(cmds, source, target):
    apply_state(cmds, display_state(cmds, source), target)


def inherit(params, context):
    from cgi_pipeline.capabilities.maya.rig.rig_ops_common import cmds_from, receipt, started, undo_mutation
    from cgi_pipeline.capabilities.maya.asset.mesh_reduce.geometry import resolve
    from cgi_pipeline.capabilities.maya.asset.proxy_rig import membership
    cmds = cmds_from(context)

    def run():
        mode = params.get("mode", "inspect")
        if mode not in ("inspect", "apply"):
            raise ValueError("mode must be inspect or apply")
        groups = params.get("groups")
        if not isinstance(groups, list) or not groups:
            raise ValueError("groups must be a non-empty list")
        prepared, targets, all_sources = [], set(), set()
        for group in groups:
            sources, target = group.get("sources"), group.get("target", {})
            if not isinstance(sources, list) or not sources:
                raise ValueError("Each group requires explicit sources")
            target_path = target.get("path")
            target_shape = resolve(target_path, target.get("expected_uuid"))
            if target_path in targets:
                raise ValueError("Duplicate target")
            targets.add(target_path)
            states = []
            for source in sources:
                path = source.get("path")
                shape = resolve(path, source.get("expected_uuid"))
                all_sources.add(path)
                states.append({"display": visibility_context(cmds, path),
                    "shape": display_state(cmds, shape),
                    "memberships": membership.inspect(cmds, path, shape, {})})
            if any(state != states[0] for state in states):
                raise ValueError("Sources have incompatible display, ancestor or set/layer mechanisms")
            state = states[0]
            if visibility_context(cmds, target_path)["ancestors"] != state["display"]["ancestors"]:
                raise ValueError("Target must retain the same ancestor controls; reparent explicitly first")
            # Check both destinations and layer ownership before the batch mutates.
            for node, values in ((target_path, state["display"]["local"]), (target_shape, state["shape"])):
                for attr, value in values.items():
                    incoming = sorted(cmds.listConnections(node + "." + attr, source=True, destination=False, plugs=True) or [])
                    if incoming and incoming != value["inputs"]:
                        raise ValueError("Conflicting target display input: " + node + "." + attr)
            target_members = membership.inspect(cmds, target_path, target_shape, {})
            for label, layers in target_members["display_layers"].items():
                if layers and layers != state["memberships"]["display_layers"][label]:
                    raise ValueError("Target belongs to a different display layer")
            prepared.append((group, target_path, target_shape, state))
        if targets & all_sources:
            raise ValueError("A target cannot also be a source in this batch")

        def mutate():
            rows = []
            for group, path, shape, state in prepared:
                if mode == "apply":
                    apply_state(cmds, state["display"]["local"], path)
                    apply_state(cmds, state["shape"], shape)
                    membership.transfer(cmds, {"memberships": state["memberships"]}, path)
                    if visibility_context(cmds, path) != state["display"] or display_state(cmds, shape) != state["shape"]:
                        raise RuntimeError("Inherited display verification failed")
                rows.append({"sources": group["sources"], "target": group["target"],
                    "preserved_ancestors": [v["path"] for v in state["display"]["ancestors"]],
                    "display_layers": state["memberships"]["display_layers"],
                    "local_state": state["display"]["local"], "shape_state": state["shape"],
                    "compatible": True, "applied": mode == "apply"})
            return {"mode": mode, "rows": rows}
        if mode == "inspect":
            return mutate()
        if not cmds.undoInfo(query=True, state=True):
            raise ValueError("Native undo must be enabled")
        return undo_mutation(cmds, "maya.scene.display.inherit", mutate)
    return receipt(context, "maya.scene.display.inherit", started(), params,
        "DISPLAY_INHERIT_FAILED", "Keep compatible sources and target under their existing control ancestors.", run)
