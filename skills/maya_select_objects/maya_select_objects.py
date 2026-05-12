"""
Maya 对象选择 — 选择、取消选择、或聚焦查看对象

支持：按名称选择、按类型选择、追加/替换、Frame Selected 聚焦
"""
import time
import traceback

from core.bootstrap import PROJECT_ROOT
from core.receipt import make_receipt


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    
    # 支持多种选择方式
    names = params.get('names', [])          # 按名称列表选择
    pattern = params.get('pattern', '')       # 按通配符模式选择，如 'body_*'
    node_type = params.get('type', '')        # 按类型选择，如 'mesh', 'joint'
    action = params.get('action', 'replace')  # 'replace' | 'add' | 'remove' | 'clear'
    frame_selected = params.get('frame_selected', False)  # 选择后是否聚焦视口
    
    try:
        import maya.cmds as cmds
        
        # ── 清空选择 ──
        if action == 'clear':
            cmds.select(clear=True)
            return make_receipt(
                skill_id='maya_select_objects',
                status='SUCCESS',
                start_time=t0,
                summary_action="清空选择",
                outputs={'result': {'selected': [], 'count': 0}},
                items=[{'name': '选择', 'detail': '已清空'}],
            )
        
        # ── 确定要选择的对象 ──
        targets = []
        
        if names:
            # 按名称列表
            targets = [n for n in names if cmds.objExists(n)]
            not_found = [n for n in names if not cmds.objExists(n)]
        elif pattern:
            # 按通配符模式
            targets = cmds.ls(pattern) or []
            not_found = []
        elif node_type:
            # 按节点类型
            if node_type in ('mesh', 'nurbsCurve', 'nurbsSurface', 'camera', 'locator'):
                # 对 shape 类型，返回其 transform 父节点
                shapes = cmds.ls(type=node_type) or []
                targets = []
                for s in shapes:
                    parent = cmds.listRelatives(s, parent=True, fullPath=False)
                    if parent:
                        targets.append(parent[0])
                targets = list(set(targets))
            else:
                targets = cmds.ls(type=node_type) or []
            not_found = []
        else:
            return make_receipt(
                skill_id='maya_select_objects',
                status='ERROR',
                start_time=t0,
                summary_action="缺少参数",
                error="必须提供 names、pattern 或 type 参数之一"
            )
        
        if not targets:
            return make_receipt(
                skill_id='maya_select_objects',
                status='SUCCESS',
                start_time=t0,
                summary_action="未找到匹配对象",
                outputs={'result': {
                    'selected': [],
                    'count': 0,
                    'not_found': not_found if 'not_found' in dir() else [],
                }},
                items=[{'name': '选择', 'detail': '无匹配对象'}],
            )
        
        # ── 执行选择 ──
        if action == 'replace':
            cmds.select(targets, replace=True)
        elif action == 'add':
            cmds.select(targets, add=True)
        elif action == 'remove':
            cmds.select(targets, deselect=True)
        
        # ── 聚焦视口 ──
        if frame_selected:
            cmds.viewFit()  # Frame Selected：聚焦选中对象
        
        # 获取最终选择结果
        final_selection = cmds.ls(selection=True) or []
        
        result = {
            'selected': final_selection,
            'count': len(final_selection),
        }
        if 'not_found' in dir() and not_found:
            result['not_found'] = not_found
        
        return make_receipt(
            skill_id='maya_select_objects',
            status='SUCCESS',
            start_time=t0,
            summary_action=f"选择了 {len(final_selection)} 个对象",
            outputs={'result': result},
            items=[{'name': '选择结果', 'detail': f"{len(final_selection)} 个对象已选择"}],
            report_content=f"已选择: {', '.join(final_selection[:10])}" + 
                          (f" ...等共 {len(final_selection)} 个" if len(final_selection) > 10 else "")
        )
    
    except Exception as e:
        return make_receipt(
            skill_id='maya_select_objects',
            status='ERROR',
            start_time=t0,
            summary_action="选择对象失败",
            error=str(e),
            recovery_hint=traceback.format_exc()
        )
