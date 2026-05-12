import time
import traceback
from core.receipt import make_receipt

def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    node = params.get('node')
    full_path = params.get('full_path', True)
    
    try:
        import maya.cmds as cmds
        
        children = []
        if not node:
            # 获取顶层所有节点
            children = cmds.ls(assemblies=True, long=full_path) or []
        else:
            if not cmds.objExists(node):
                raise ValueError(f"节点不存在: {node}")
            # 获取所有子节点
            children = cmds.listRelatives(node, allDescendents=True, fullPath=full_path) or []
            # listRelatives 返回的是从底向上的顺序，反转它以便按层级从上往下显示
            children.reverse()
            
        return make_receipt(
            skill_id='maya_get_hierarchy',
            status='SUCCESS',
            start_time=t0,
            summary_action=f"查询层级: {node or '根层级'}",
            outputs={'hierarchy': children},
            items=[{'name': '节点数量', 'detail': f"{len(children)} 个子节点"}],
            report_content="\n".join(children[:100]) + ("\n...等" if len(children)>100 else "")
        )

    except Exception as e:
        return make_receipt(
            skill_id='maya_get_hierarchy',
            status='ERROR',
            start_time=t0,
            summary_action=f"查询层级失败: {node}",
            error=str(e),
            recovery_hint=traceback.format_exc()
        )
