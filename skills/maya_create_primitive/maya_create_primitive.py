import time
import traceback
from core.receipt import make_receipt

def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    prim_type = params.get('primitive_type', 'cube').lower()
    name = params.get('name')
    radius = params.get('radius')
    
    try:
        import maya.cmds as cmds
        
        kwargs = {}
        if name:
            kwargs['name'] = name
        if radius is not None:
            if prim_type in ['cube', 'plane']:
                kwargs['width'] = radius
                kwargs['height'] = radius
                kwargs['depth'] = radius
            else:
                kwargs['radius'] = radius

        # 创建动作包裹在 undo 块中
        cmds.undoInfo(openChunk=True)
        try:
            if prim_type == 'cube':
                result = cmds.polyCube(**kwargs)
            elif prim_type == 'sphere':
                result = cmds.polySphere(**kwargs)
            elif prim_type == 'plane':
                result = cmds.polyPlane(**kwargs)
            elif prim_type == 'cylinder':
                result = cmds.polyCylinder(**kwargs)
            elif prim_type == 'cone':
                result = cmds.polyCone(**kwargs)
            elif prim_type == 'torus':
                result = cmds.polyTorus(**kwargs)
            else:
                raise ValueError(f"不支持的几何体类型: {prim_type}")
        finally:
            cmds.undoInfo(closeChunk=True)
            
        return make_receipt(
            skill_id='maya_create_primitive',
            status='SUCCESS',
            start_time=t0,
            summary_action=f"创建 {prim_type}",
            outputs={'nodes': result},
            items=[{'name': '创建的节点', 'detail': str(result)}]
        )

    except Exception as e:
        return make_receipt(
            skill_id='maya_create_primitive',
            status='ERROR',
            start_time=t0,
            summary_action=f"创建 {prim_type} 失败",
            error=str(e),
            recovery_hint=traceback.format_exc()
        )
