import time
import os, sys
import maya.cmds as cmds

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item, _sec_to_min


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    target_nodes = params.get('target_nodes', [])
    source_path = payload.get('source_path', '')

    current_scene = cmds.file(query=True, sceneName=True) or ''
    scene_name = os.path.basename(current_scene or source_path or 'untitled')

    if source_path:
        cmds.file(source_path, open=True, force=True)

    if not target_nodes:
        meshes = cmds.ls(type='mesh', long=True) or []
        transforms = set()
        for mesh in meshes:
            parent = cmds.listRelatives(mesh, parent=True, fullPath=True)
            if parent:
                transforms.add(parent[0])
        target_nodes = sorted(transforms)

    if not target_nodes:
        return make_receipt('maya_freeze_transforms', 'SUCCESS', t0,
                            summary_input=scene_name,
                            summary_action='无需冻结 — 场景中没有 mesh')

    items = []
    errors = []

    cmds.undoInfo(openChunk=True, chunkName='maya_freeze_transforms')
    try:
        for node in target_nodes:
            step_t0 = time.time()
            if not cmds.objExists(node):
                errors.append(f'节点不存在: {node}')
                continue
            try:
                cmds.makeIdentity(node, apply=True, translate=True, rotate=True, scale=True,
                                  normal=False, preserveNormals=True)
                cmds.delete(node, constructionHistory=True)
                short = node.split('|')[-1]
                items.append(make_item(
                    name=short,
                    detail='冻结 TRS + 清除历史',
                    elapsed_min=_sec_to_min(time.time() - step_t0),
                ))
            except Exception as e:
                errors.append(f'{node}: {e}')
    finally:
        cmds.undoInfo(closeChunk=True)

    status = 'SUCCESS' if not errors else 'PARTIAL'
    return make_receipt(
        skill_id='maya_freeze_transforms',
        status=status,
        start_time=t0,
        summary_input=scene_name,
        summary_action=f'冻结变换 + 清除历史',
        summary_count=len(items),
        summary_label='节点',
        items=items,
        error='; '.join(errors) if errors else '',
    )
