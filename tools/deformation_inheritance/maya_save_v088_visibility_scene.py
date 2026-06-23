"""Save current scene as v088 visibility diagnostic scene."""

from __future__ import annotations

import traceback
from pathlib import Path

import maya.cmds as cmds


OUT_SCENE = Path(r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\test_v088_A_visibility_probe.ma")

try:
    OUT_SCENE.parent.mkdir(parents=True, exist_ok=True)
    cmds.file(rename=str(OUT_SCENE).replace("\\", "/"))
    cmds.file(save=True, type="mayaAscii", force=True)
    result = {"status": "SUCCESS", "scene": cmds.file(q=True, sceneName=True)}
except Exception as exc:
    result = {"status": "ERROR", "error": str(exc), "traceback": traceback.format_exc()}
