"""Set one Maya plug with explicit type and lock handling."""

from __future__ import annotations

import time

import maya.cmds as cmds

from cgi_pipeline.core.receipt import make_receipt


def execute(payload: dict) -> dict:
    started = time.time()
    params = payload.get("parameters") or {}
    plug = str(params.get("plug") or "").strip()
    if not plug:
        node = str(params.get("node") or "").strip()
        attribute = str(params.get("attribute") or "").strip()
        plug = f"{node}.{attribute}" if node and attribute else ""
    if not plug or "." not in plug:
        return make_receipt("maya_set_attribute", "ERROR", started,
                            input=params, error="需要 plug 或 node+attribute")
    if "value" not in params and "values" not in params:
        return make_receipt("maya_set_attribute", "ERROR", started,
                            input=params, error="缺少 value")
    try:
        if not cmds.objExists(plug):
            return make_receipt("maya_set_attribute", "ERROR", started,
                                input=params, error=f"属性不存在: {plug}")
        locked = bool(cmds.getAttr(plug, lock=True))
        unlock = bool(params.get("unlock", False))
        if locked and not unlock:
            return make_receipt("maya_set_attribute", "BLOCKED", started,
                                input=params, error=f"属性已锁定: {plug}",
                                recovery_hint="显式传 unlock=true 后重试。")
        if locked:
            cmds.setAttr(plug, lock=False)
        before = cmds.getAttr(plug)
        value = params.get("values", params.get("value"))
        value_type = params.get("type")
        if isinstance(value, (list, tuple)):
            if value_type:
                cmds.setAttr(plug, *value, type=value_type)
            else:
                cmds.setAttr(plug, *value)
        elif value_type:
            cmds.setAttr(plug, value, type=value_type)
        else:
            cmds.setAttr(plug, value)
        if locked:
            cmds.setAttr(plug, lock=True)
        after = cmds.getAttr(plug)
        return make_receipt("maya_set_attribute", "SUCCESS", started,
                            input=params,
                            output={"plug": plug, "before": before, "after": after,
                                    "restored_lock": locked},
                            summary_input=plug, summary_action="set", summary_count=1)
    except Exception as exc:
        return make_receipt("maya_set_attribute", "ERROR", started,
                            input=params, error=str(exc),
                            recovery_hint="检查 plug 类型、值形状及 Maya 属性锁定状态。")
