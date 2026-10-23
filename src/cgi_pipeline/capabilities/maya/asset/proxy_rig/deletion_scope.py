"""Check every mesh child removed with a transform, without optimizing Orig data."""


def inspect(cmds, transform, outputs, history=None):
    children = cmds.listRelatives(transform, shapes=True, fullPath=True, type="mesh") or list(outputs)
    owned = set(history or []) | set(children) | {transform}
    if history is None:
        for shape in outputs:
            owned.update(cmds.listHistory(shape, pruneDagObjects=True) or [])
    records, external = [], []
    for shape in children:
        records.append({"path": shape, "uuid": (cmds.ls(shape, uuid=True) or [""])[0],
                        "is_output": shape in outputs})
        if len(cmds.listRelatives(shape, allParents=True, fullPath=True) or []) > 1:
            external.append(shape + ":instanced_child_shape")
        for target in cmds.listConnections(shape, source=False, destination=True) or []:
            kind = cmds.nodeType(target)
            if target in owned or kind == "shadingEngine":
                continue
            if kind == "objectSet" and not (set(cmds.listConnections(target, type="geometryFilter") or []) - owned):
                continue
            external.append(shape + " -> " + target)
        if cmds.objExists(shape + ".message"):
            targets = set(cmds.listConnections(shape + ".message", source=False, destination=True) or []) - owned
            external.extend(shape + ".message -> " + target for target in targets)
    return {"shapes": sorted(records, key=lambda item: item["path"]), "external_consumers": sorted(set(external))}
