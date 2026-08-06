import maya.cmds as cmds
import os
import time

from core.receipt import make_receipt, make_item


GEO_GROUP_CANDIDATES = ['geo', 'Group', 'cache', 'Geo', 'GEO']


def find_geo_group(custom_name=None):
    candidates = [custom_name] + GEO_GROUP_CANDIDATES if custom_name else GEO_GROUP_CANDIDATES
    for name in candidates:
        for pattern in [f'*|{name}', f'*|*|{name}', name]:
            matches = cmds.ls(pattern, long=True, type='transform') or []
            if matches:
                return matches[0]
    return None


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    geo_group = params.get('geo_group', '')

    grp = find_geo_group(geo_group or None)
    if not grp:
        return make_receipt('maya_conform_normals', 'ERROR', t0,
                            error='未找到 geo/Group/cache 组')

    meshes = cmds.listRelatives(grp, allDescendents=True, type='mesh', fullPath=True) or []
    meshes = [m for m in meshes if not cmds.getAttr(m + '.intermediateObject')]

    if not meshes:
        return make_receipt('maya_conform_normals', 'ERROR', t0,
                            error=f'{grp} 下无有效 mesh')

    items = []
    errors = []
    cmds.undoInfo(openChunk=True, chunkName='maya_conform_normals')
    try:
        for shape in meshes:
            t_mesh = time.time()
            short = shape.split('|')[-1]
            try:
                cmds.polyNormal(shape, normalMode=2, userNormalMode=False, constructionHistory=False)
                elapsed = round((time.time() - t_mesh) / 60, 4)
                items.append(make_item(name=short, detail='conform 完成', elapsed_min=elapsed))
            except Exception as e:
                errors.append(f'{short}: {e}')
    finally:
        cmds.undoInfo(closeChunk=True)

    status = 'SUCCESS' if not errors else 'PARTIAL'
    scene_name = os.path.basename(cmds.file(query=True, sceneName=True) or 'untitled')

    receipt = make_receipt(
        api_id='maya_conform_normals',
        status=status,
        start_time=t0,
        summary_input=scene_name,
        summary_action=f'Conform 法线 — {len(meshes)} 个 mesh',
        summary_count=len(items),
        summary_label='mesh',
        items=items,
        error='; '.join(errors) if errors else '',
    )
    return receipt
