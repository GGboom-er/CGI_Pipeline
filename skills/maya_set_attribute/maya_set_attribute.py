import time
import traceback
from core.receipt import make_receipt

def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    node = params.get('node')
    attr = params.get('attribute')
    value = params.get('value')
    attr_type = params.get('type')
    
    if not node or not attr:
        return make_receipt('maya_set_attribute', 'ERROR', t0, error='缺少 node 或 attribute 参数')
        
    try:
        import maya.cmds as cmds
        
        full_attr = f"{node}.{attr}"
        if not cmds.objExists(full_attr):
            raise ValueError(f"属性不存在: {full_attr}")
            
        cmds.undoInfo(openChunk=True)
        try:
            if attr_type == 'string' or isinstance(value, str):
                cmds.setAttr(full_attr, value, type="string")
            elif isinstance(value, list) or isinstance(value, tuple):
                cmds.setAttr(full_attr, *value)
            else:
                cmds.setAttr(full_attr, value)
        finally:
            cmds.undoInfo(closeChunk=True)
            
        return make_receipt(
            skill_id='maya_set_attribute',
            status='SUCCESS',
            start_time=t0,
            summary_action=f"设置 {full_attr} = {value}",
            items=[{'name': '修改的属性', 'detail': f"{full_attr} -> {value}"}]
        )

    except Exception as e:
        return make_receipt(
            skill_id='maya_set_attribute',
            status='ERROR',
            start_time=t0,
            summary_action=f"设置属性失败: {node}.{attr}",
            error=str(e),
            recovery_hint=traceback.format_exc()
        )
