"""
Maya 场景总览 — AI 理解场景全貌的基础能力
 
返回：对象统计、材质列表、相机、灯光、面数、帧范围等。
"""
import time
import traceback

from core.bootstrap import PROJECT_ROOT
from core.receipt import make_receipt


def execute(payload: dict) -> dict:
    t0 = time.time()
    
    try:
        import maya.cmds as cmds
        
        # ── 1. 基础场景信息 ──
        scene_path = cmds.file(q=True, sceneName=True) or '(未保存)'
        modified = cmds.file(q=True, modified=True)
        
        # ── 2. 对象统计 ──
        all_transforms = cmds.ls(type='transform') or []
        all_meshes = cmds.ls(type='mesh') or []
        all_curves = cmds.ls(type='nurbsCurve') or []
        all_joints = cmds.ls(type='joint') or []
        all_cameras = cmds.ls(type='camera') or []
        all_lights = cmds.ls(lights=True) or []
        all_locators = cmds.ls(type='locator') or []
        
        # 默认相机列表（过滤掉）
        default_cams = {'frontShape', 'perspShape', 'sideShape', 'topShape'}
        user_cameras = [c for c in all_cameras if c not in default_cams]
        
        # ── 3. 面数统计 ──
        total_verts = 0
        total_faces = 0
        mesh_details = []
        warnings = []
        for mesh in all_meshes:
            try:
                parent = cmds.listRelatives(mesh, parent=True, fullPath=False)
                name = parent[0] if parent else mesh
                vc = cmds.polyEvaluate(mesh, vertex=True)
                fc = cmds.polyEvaluate(mesh, face=True)
                total_verts += vc
                total_faces += fc
                mesh_details.append({'name': name, 'verts': vc, 'faces': fc})
            except Exception as e:
                warnings.append({'name': mesh, 'detail': f'polyEvaluate 失败: {e}'})
        
        # Top 10 最重的 mesh
        mesh_details.sort(key=lambda x: x['faces'], reverse=True)
        top_meshes = mesh_details[:10]
        
        # ── 4. 材质统计 ──
        all_shading_engines = cmds.ls(type='shadingEngine') or []
        # 过滤默认节点
        default_se = {'initialShadingGroup', 'initialParticleSE'}
        user_se = [se for se in all_shading_engines if se not in default_se]
        
        materials = []
        for se in user_se:
            mat = cmds.ls(cmds.listConnections(se + '.surfaceShader') or [], materials=True)
            if mat:
                mat_type = cmds.nodeType(mat[0])
                materials.append({'name': mat[0], 'type': mat_type, 'shading_engine': se})
        
        # ── 5. 时间轴 ──
        frame_start = cmds.playbackOptions(q=True, minTime=True)
        frame_end = cmds.playbackOptions(q=True, maxTime=True)
        current_frame = cmds.currentTime(q=True)
        fps = cmds.currentUnit(q=True, time=True)
        
        # ── 6. 渲染器 ──
        renderer = cmds.getAttr('defaultRenderGlobals.currentRenderer') if cmds.objExists('defaultRenderGlobals') else 'unknown'
        
        # ── 7. 引用文件 ──
        references = cmds.file(q=True, reference=True) or []
        
        # ── 8. 未知节点 ──
        unknown_nodes = cmds.ls(type='unknown') or []
        unknown_dag = cmds.ls(type='unknownDag') or []
        
        # ── 9. 插件 ──
        loaded_plugins = cmds.pluginInfo(q=True, listPlugins=True) or []
        
        # ── 10. 单位 ──
        linear_unit = cmds.currentUnit(q=True, linear=True)
        
        # ── 组装结果 ──
        scene_info = {
            'scene': {
                'path': scene_path,
                'modified': modified,
                'linear_unit': linear_unit,
                'renderer': renderer,
            },
            'timeline': {
                'start': frame_start,
                'end': frame_end,
                'current': current_frame,
                'fps': fps,
            },
            'statistics': {
                'transforms': len(all_transforms),
                'meshes': len(all_meshes),
                'total_vertices': total_verts,
                'total_faces': total_faces,
                'curves': len(all_curves),
                'joints': len(all_joints),
                'cameras': len(all_cameras),
                'user_cameras': len(user_cameras),
                'lights': len(all_lights),
                'locators': len(all_locators),
                'materials': len(materials),
                'references': len(references),
                'unknown_nodes': len(unknown_nodes) + len(unknown_dag),
                'plugins_loaded': len(loaded_plugins),
            },
            'top_meshes_by_faces': top_meshes,
            'materials': materials[:20],  # 最多显示 20 个
            'user_cameras': [cmds.listRelatives(c, parent=True, fullPath=False)[0] 
                           for c in user_cameras 
                           if cmds.listRelatives(c, parent=True)],
            'lights': [cmds.listRelatives(l, parent=True, fullPath=False)[0] 
                      for l in (cmds.ls(lights=True) or [])
                      if cmds.listRelatives(l, parent=True)],
            'references': references[:10],
            'warnings': warnings[:20],
        }
        
        # 生成简报
        summary_lines = [
            f"场景: {scene_path}",
            f"Mesh: {len(all_meshes)} 个 | 顶点: {total_verts:,} | 面: {total_faces:,}",
            f"骨骼: {len(all_joints)} | 材质: {len(materials)} | 灯光: {len(all_lights)}",
            f"帧范围: {frame_start:.0f}-{frame_end:.0f} @ {fps}",
        ]
        if unknown_nodes or unknown_dag:
            summary_lines.append(f"⚠ 未知节点: {len(unknown_nodes) + len(unknown_dag)} 个")
        
        items = [{'name': '场景总览', 'detail': ' | '.join(summary_lines)}]
        items.extend(warnings[:19])

        return make_receipt(
            api_id='maya_get_scene_info',
            status='SUCCESS',
            start_time=t0,
            summary_action="采集场景信息",
            outputs={'result': scene_info},
            items=items,
            report_content='\n'.join(summary_lines)
        )
    
    except Exception as e:
        return make_receipt(
            api_id='maya_get_scene_info',
            status='ERROR',
            start_time=t0,
            summary_action="采集场景信息失败",
            error=str(e),
            recovery_hint=traceback.format_exc()
        )
