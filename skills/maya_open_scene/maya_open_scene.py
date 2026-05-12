"""
Maya 打开场景 — 打开 .ma/.mb 文件

支持：强制打开（忽略修改）、打开新空场景
"""
import time
import traceback
from pathlib import Path

from core.bootstrap import PROJECT_ROOT
from core.receipt import make_receipt


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    file_path = params.get('file_path', '')
    new_scene = params.get('new_scene', False)   # 如果 True，创建新空场景
    force = params.get('force', True)             # 忽略未保存修改，直接打开
    
    try:
        import maya.cmds as cmds
        
        # ── 新建空场景 ──
        if new_scene:
            cmds.file(new=True, force=force)
            return make_receipt(
                skill_id='maya_open_scene',
                status='SUCCESS',
                start_time=t0,
                summary_action="新建空场景",
                outputs={'result': {'action': 'new'}},
                items=[{'name': '操作', 'detail': '已创建新空场景'}],
            )
        
        # ── 打开文件 ──
        if not file_path:
            return make_receipt(
                skill_id='maya_open_scene',
                status='ERROR',
                start_time=t0,
                summary_action="缺少参数",
                error="必须提供 file_path 或设置 new_scene=True"
            )
        
        # 规范化路径
        file_path = str(Path(file_path)).replace('\\', '/')
        
        if not Path(file_path).exists():
            return make_receipt(
                skill_id='maya_open_scene',
                status='ERROR',
                start_time=t0,
                summary_action="文件不存在",
                error=f"文件不存在: {file_path}"
            )
        
        # 确定文件类型
        ext = Path(file_path).suffix.lower()
        file_type = 'mayaAscii' if ext == '.ma' else 'mayaBinary' if ext == '.mb' else None
        
        if not file_type:
            return make_receipt(
                skill_id='maya_open_scene',
                status='ERROR',
                start_time=t0,
                summary_action="不支持的格式",
                error=f"不支持的文件格式: {ext}，仅支持 .ma/.mb"
            )
        
        # 打开文件
        cmds.file(file_path, open=True, force=force, type=file_type)
        
        # 获取打开后的信息
        scene_name = cmds.file(q=True, sceneName=True)
        mesh_count = len(cmds.ls(type='mesh') or [])
        joint_count = len(cmds.ls(type='joint') or [])
        
        return make_receipt(
            skill_id='maya_open_scene',
            status='SUCCESS',
            start_time=t0,
            summary_action=f"打开 {Path(file_path).name}",
            outputs={
                'output_path': scene_name,
                'result': {
                    'action': 'open',
                    'meshes': mesh_count,
                    'joints': joint_count,
                },
            },
            items=[{'name': '已打开', 'detail': file_path}],
            report_content=f"已打开: {file_path}\nMesh: {mesh_count} | 骨骼: {joint_count}"
        )
    
    except Exception as e:
        return make_receipt(
            skill_id='maya_open_scene',
            status='ERROR',
            start_time=t0,
            summary_action="打开场景失败",
            error=str(e),
            recovery_hint=traceback.format_exc()
        )
