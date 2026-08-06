import time
import os, sys
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item, _sec_to_min


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    threshold = float(params.get('threshold', 0.001))
    source_path = payload.get('source_path', '')

    current_scene = cmds.file(query=True, sceneName=True) or ''
    scene_name = os.path.basename(current_scene or source_path or 'untitled')

    if source_path and not current_scene:
        if os.path.isfile(source_path):
            cmds.file(source_path, open=True, force=True)
        else:
            return make_receipt('maya_clean_skinweights', 'ERROR', t0,
                               summary_input=scene_name,
                               error=f'源文件不存在: {source_path}')

    skin_clusters = cmds.ls(type='skinCluster') or []
    if not skin_clusters:
        return make_receipt('maya_clean_skinweights', 'SUCCESS', t0,
                            summary_input=scene_name,
                            summary_action='未找到 skinCluster，无需清理')

    total_cleaned = 0
    items = []

    cmds.undoInfo(openChunk=True, chunkName='maya_clean_skinweights')
    try:
        for sc_name in skin_clusters:
            step_t0 = time.time()
            geo = cmds.skinCluster(sc_name, query=True, geometry=True)
            if not geo:
                continue

            sel = om.MSelectionList()
            sel.add(sc_name)
            sc_obj = sel.getDependNode(0)
            fn_skin = oma.MFnSkinCluster(sc_obj)
            influence_count = len(fn_skin.influenceObjects())

            sel_geo = om.MSelectionList()
            sel_geo.add(geo[0])
            dag_path = sel_geo.getDagPath(0)

            try:
                dag_path.extendToShape()
            except RuntimeError:
                continue

            if not dag_path.hasFn(om.MFn.kMesh):
                continue

            fn_mesh = om.MFnMesh(dag_path)
            vert_count = fn_mesh.numVertices
            fn_comp = om.MFnSingleIndexedComponent()
            vert_comp = fn_comp.create(om.MFn.kMeshVertComponent)
            fn_comp.addElements(list(range(vert_count)))

            weights, _ = fn_skin.getWeights(dag_path, vert_comp)

            cleaned_in_this = 0
            new_weights = om.MDoubleArray(weights)

            for v in range(vert_count):
                base = v * influence_count
                modified = False
                for j in range(influence_count):
                    idx = base + j
                    if 0 < new_weights[idx] < threshold:
                        new_weights[idx] = 0.0
                        modified = True
                if modified:
                    cleaned_in_this += 1
                    total = sum(new_weights[base + j] for j in range(influence_count))
                    if total > 0:
                        for j in range(influence_count):
                            new_weights[base + j] /= total

            if cleaned_in_this > 0:
                influence_indices = om.MIntArray(range(influence_count))
                fn_skin.setWeights(dag_path, vert_comp, influence_indices, new_weights)

            total_cleaned += cleaned_in_this
            items.append(make_item(
                name=sc_name,
                detail=f'清理 {cleaned_in_this:,}/{vert_count:,} 顶点 (阈值 {threshold})',
                elapsed_min=_sec_to_min(time.time() - step_t0),
            ))
    finally:
        cmds.undoInfo(closeChunk=True)

    return make_receipt(
        api_id='maya_clean_skinweights',
        status='SUCCESS',
        start_time=t0,
        summary_input=scene_name,
        summary_action='清理蒙皮权重噪声',
        summary_count=len(items),
        summary_label='skinCluster',
        items=items,
    )
