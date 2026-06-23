"""保存当前 v087 对比场景。"""

from __future__ import annotations

import traceback

import maya.cmds as cmds


try:
    cmds.file(save=True, type="mayaAscii", force=True)
    result = {"status": "SUCCESS", "scene": cmds.file(q=True, sceneName=True)}
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
