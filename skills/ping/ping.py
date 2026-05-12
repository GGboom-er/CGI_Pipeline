import time
import os, sys

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt


def execute(payload: dict) -> dict:
    t0 = time.time()
    import maya.cmds as cmds
    try:
        maya_ver = cmds.about(version=True)
    except Exception:
        maya_ver = 'Unknown'

    return make_receipt(
        skill_id='ping',
        status='SUCCESS',
        start_time=t0,
        summary_action=f'心跳测试 — Maya {maya_ver}',
    )
