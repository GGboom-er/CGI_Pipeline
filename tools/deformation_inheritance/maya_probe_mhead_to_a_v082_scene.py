"""Read-only scene probe for the v082 M_Head_base -> A reboot."""

from __future__ import annotations

import maya.cmds as cmds


SOURCE = "M_Head_base"
TARGET = "A"


def _long(name: str) -> str | None:
    found = cmds.ls(name, long=True) or []
    return found[0] if found else None


def _skin(transform: str) -> str | None:
    history = cmds.listHistory(transform, pruneDagObjects=True) or []
    skins = []
    for node in history:
        try:
            if cmds.nodeType(node) == "skinCluster":
                skins.append(node)
        except Exception:
            pass
    return sorted(set(skins))[0] if skins else None


def _mesh_stats(transform: str) -> dict:
    if not transform:
        return {"exists": False}
    shapes = cmds.listRelatives(transform, shapes=True, noIntermediate=True, fullPath=True) or []
    return {
        "exists": True,
        "long": transform,
        "shape": shapes[0] if shapes else None,
        "vertex_count": int(cmds.polyEvaluate(transform, vertex=True) or 0),
        "face_count": int(cmds.polyEvaluate(transform, face=True) or 0),
        "skinCluster": _skin(transform),
        "bbox": [float(v) for v in (cmds.exactWorldBoundingBox(transform) or [])],
    }


source = _long(SOURCE)
target = _long(TARGET)

result = {
    "status": "SUCCESS",
    "scene": cmds.file(q=True, sceneName=True),
    "source": _mesh_stats(source),
    "target": _mesh_stats(target),
}
