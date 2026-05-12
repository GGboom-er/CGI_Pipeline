"""
Maya 对象详情查询 — 获取单个对象的全部属性信息

返回：变换、形状、材质、修改器、连接、包围盒等。
"""
import time
import traceback

from core.bootstrap import PROJECT_ROOT
from core.receipt import make_receipt


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    object_name = params.get('object_name', '')
    
    if not object_name:
        return make_receipt(
            skill_id='maya_get_object_info',
            status='ERROR',
            start_time=t0,
            summary_action="缺少参数",
            error="必须提供 object_name 参数"
        )
    
    try:
        import maya.cmds as cmds
        
        if not cmds.objExists(object_name):
            return make_receipt(
                skill_id='maya_get_object_info',
                status='ERROR',
                start_time=t0,
                summary_action="对象不存在",
                error=f"对象 '{object_name}' 在场景中不存在"
            )
        
        node_type = cmds.nodeType(object_name)
        
        info = {
            'name': object_name,
            'node_type': node_type,
            'full_path': cmds.ls(object_name, long=True)[0] if cmds.ls(object_name, long=True) else object_name,
        }
        warnings = []
        
        # ── Transform 属性 ──
        if cmds.objectType(object_name, isAType='transform'):
            info['transform'] = {
                'translate': cmds.xform(object_name, q=True, translation=True, worldSpace=True),
                'rotate': cmds.xform(object_name, q=True, rotation=True, worldSpace=True),
                'scale': cmds.xform(object_name, q=True, scale=True, relative=True),
                'pivot': cmds.xform(object_name, q=True, rotatePivot=True, worldSpace=True),
            }
            
            # 包围盒
            try:
                bbox = cmds.exactWorldBoundingBox(object_name)
                info['bounding_box'] = {
                    'min': bbox[:3],
                    'max': bbox[3:],
                    'size': [bbox[3]-bbox[0], bbox[4]-bbox[1], bbox[5]-bbox[2]],
                }
            except Exception as e:
                warnings.append({'name': object_name, 'detail': f'包围盒计算失败: {e}'})
            
            # 子节点
            children = cmds.listRelatives(object_name, children=True, fullPath=False) or []
            info['children'] = children
            
            # 父节点
            parent = cmds.listRelatives(object_name, parent=True, fullPath=False)
            info['parent'] = parent[0] if parent else None
            
            # Shape 节点
            shapes = cmds.listRelatives(object_name, shapes=True, fullPath=False) or []
            info['shapes'] = shapes
            
            # 如果有 mesh shape
            mesh_shapes = [s for s in shapes if cmds.nodeType(s) == 'mesh']
            if mesh_shapes:
                mesh = mesh_shapes[0]
                info['mesh'] = {
                    'vertices': cmds.polyEvaluate(mesh, vertex=True),
                    'faces': cmds.polyEvaluate(mesh, face=True),
                    'edges': cmds.polyEvaluate(mesh, edge=True),
                    'triangles': cmds.polyEvaluate(mesh, triangle=True),
                    'uv_sets': cmds.polyUVSet(mesh, q=True, allUVSets=True) or [],
                }
                
                # 材质
                shading_groups = cmds.listConnections(mesh, type='shadingEngine') or []
                shading_groups = list(set(shading_groups))
                mats = []
                for sg in shading_groups:
                    mat = cmds.ls(cmds.listConnections(sg + '.surfaceShader') or [], materials=True)
                    if mat:
                        mats.append({'name': mat[0], 'type': cmds.nodeType(mat[0]), 'sg': sg})
                info['materials'] = mats
            
            # 修改器/历史
            history = cmds.listHistory(object_name, pruneDagObjects=True) or []
            # 过滤掉 shape 和 groupId 等非关键节点
            skip_types = {'mesh', 'groupId', 'shadingEngine', 'materialInfo', 
                         'renderLayer', 'displayLayer', 'objectSet'}
            deformers = [{'name': h, 'type': cmds.nodeType(h)} 
                        for h in history 
                        if cmds.nodeType(h) not in skip_types]
            info['deformers_history'] = deformers[:20]
            
            # 可见性
            info['visibility'] = cmds.getAttr(f'{object_name}.visibility')
            
            # 锁定状态
            locked_attrs = []
            for attr in ['tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz']:
                if cmds.getAttr(f'{object_name}.{attr}', lock=True):
                    locked_attrs.append(attr)
            if locked_attrs:
                info['locked_attributes'] = locked_attrs
        
        # ── Joint 专属 ──
        if node_type == 'joint':
            info['joint'] = {
                'orientation': list(cmds.getAttr(f'{object_name}.jointOrient')[0]),
                'radius': cmds.getAttr(f'{object_name}.radius'),
            }
            # 子骨骼
            child_joints = cmds.listRelatives(object_name, children=True, type='joint') or []
            info['child_joints'] = child_joints
        
        # ── Camera 专属 ──
        if node_type == 'camera':
            info['camera'] = {
                'focal_length': cmds.getAttr(f'{object_name}.focalLength'),
                'near_clip': cmds.getAttr(f'{object_name}.nearClipPlane'),
                'far_clip': cmds.getAttr(f'{object_name}.farClipPlane'),
            }
        
        if warnings:
            info['warnings'] = warnings[:20]

        items = [{'name': object_name, 'detail': f"类型: {node_type}"}]
        items.extend(warnings[:19])

        return make_receipt(
            skill_id='maya_get_object_info',
            status='SUCCESS',
            start_time=t0,
            summary_action=f"查询对象 {object_name}",
            outputs={'result': info},
            items=items,
            report_content=f"对象: {object_name}\n类型: {node_type}"
        )
    
    except Exception as e:
        return make_receipt(
            skill_id='maya_get_object_info',
            status='ERROR',
            start_time=t0,
            summary_action="查询对象失败",
            error=str(e),
            recovery_hint=traceback.format_exc()
        )
