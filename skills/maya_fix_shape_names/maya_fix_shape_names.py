# skills/fix_shape_names.py
# ── Shape 形节点命名规范化与死残渣清理 ──
#
# 严格按照入库规范：
# 1. 唯一可见形状重命名为：{TransformName}Shape
# 2. 具有真实变形器连接的源形状重命名为：{TransformName}ShapeOrig
# 3. 未连接任何节点的 IntermediateObject 形节点直接拔除
# 4. 返回详细重命名和清理报告

import maya.cmds as cmds
import uuid
import time
import os, sys

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item, _sec_to_min


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    target_grp = params.get('target_group', "")
    if target_grp:
        from dccs.maya.asset_info_collector import resolve_cache_group
        target_grp = resolve_cache_group(target_grp) or target_grp
    
    source_path = payload.get('source_path', '')
    current_scene = cmds.file(query=True, sceneName=True) or ''
    scene_name = os.path.basename(current_scene or source_path or 'untitled')
    if source_path and not current_scene:
        if os.path.isfile(source_path):
            cmds.file(source_path, open=True, force=True)

    # 确定搜索范围
    if target_grp and cmds.objExists(target_grp):
        all_meshes = cmds.ls(target_grp, dag=True, type='mesh', long=True) or []
    else:
        all_meshes = cmds.ls(type='mesh', long=True) or []

    # 按 Transform 归类
    mesh_dict = {}
    for mesh in all_meshes:
        parent_tr = cmds.listRelatives(mesh, parent=True, fullPath=True)
        if not parent_tr:
            continue
        tr = parent_tr[0]
        if tr not in mesh_dict:
            mesh_dict[tr] = []
        if mesh not in mesh_dict[tr]:
            mesh_dict[tr].append(mesh)

    report_list = []
    
    cmds.undoInfo(openChunk=True, chunkName='maya_fix_shape_names')
    try:
        for tr, shapes in mesh_dict.items():
            tr_short = tr.split('|')[-1]
            
            # 排除 Reference 节点以免锁死报错
            if cmds.referenceQuery(tr, isNodeReferenced=True):
                continue

            active_shapes = []
            intermediate_shapes = []
            
            for sh in shapes:
                if cmds.getAttr(f"{sh}.intermediateObject"):
                    intermediate_shapes.append(sh)
                else:
                    active_shapes.append(sh)
                    
            # 验证活着的形状
            if len(active_shapes) == 0:
                report_list.append({'transform': tr_short, 'status': 'WARNING', 'msg': '没有活着的可见Shape节点'})
                continue
            elif len(active_shapes) > 1:
                report_list.append({'transform': tr_short, 'status': 'WARNING', 'msg': f'存在 {len(active_shapes)} 个可见Shape，无法判断唯一主Shape'})
                continue
                
            main_shape = active_shapes[0]
            
            # 判断 Orig 形状：必须有下游连接才算真正的 Orig
            true_origs = []
            dead_origs = []
            
            for sh in intermediate_shapes:
                # 检查是否有任何连接输出
                outs = cmds.listConnections(sh, source=False, destination=True)
                if outs:
                    true_origs.append(sh)
                else:
                    dead_origs.append(sh)
                    
            # 动作追踪
            actions_taken = []
            deleted_nodes = []
            
            # 1. 删死节点
            for dead in dead_origs:
                try:
                    # 尝试解锁
                    if cmds.lockNode(dead, q=True, lock=True)[0]:
                        cmds.lockNode(dead, lock=False)
                    dead_short = dead.split('|')[-1]
                    cmds.delete(dead)
                    deleted_nodes.append(dead_short)
                except Exception as e:
                    report_list.append({'transform': tr_short, 'status': 'WARNING', 'msg': f'删除死节点失败: {dead}: {e}'})
                    
            # 2. 定位唯一的 Orig
            actual_orig_to_rename = None
            if len(true_origs) == 0:
                pass # 可能只是白模，没有加变形器，也就不存在 Orig，正常
            elif len(true_origs) == 1:
                actual_orig_to_rename = true_origs[0]
            else:
                report_list.append({'transform': tr_short, 'status': 'WARNING', 'msg': f'有 {len(true_origs)} 个带有输出的 Orig 节点，可能堆叠了极其复杂的变形树，拒绝自动命名。'})
                # 但仍然可以命名 main_shape
                
            # 3. 规范化命名
            ideal_main_name = f"{tr_short}Shape"
            ideal_orig_name = f"{tr_short}ShapeOrig"
            
            # 为了防止重名的 UUID 中转机制
            # 如果理想名字被其他节点占用，先给占用的换个名字
            def _safe_rename(node_path, target_name):
                short_name = node_path.split('|')[-1]
                if short_name == target_name:
                    return target_name, False # 未改变
                    
                if cmds.objExists(target_name):
                    # 名字被占用了，强行给那个占用的改个临时名字以腾出空间
                    temp_uuid = str(uuid.uuid4()).replace('-', '')[:8]
                    cmds.rename(target_name, f"trash_{temp_uuid}")
                    
                try:
                    new_path = cmds.rename(node_path, target_name)
                    return new_path.split('|')[-1], True
                except Exception:
                    return short_name, False
                    
            main_renamed = False
            orig_renamed = False
            
            # 如果既要命名 Orig 又要命名 Main，为了防错先处理 Orig（因为 Orig 常带有重名嫌疑）
            if actual_orig_to_rename:
                old_orig_short = actual_orig_to_rename.split('|')[-1]
                new_orig_short, changed = _safe_rename(actual_orig_to_rename, ideal_orig_name)
                if changed:
                    actions_taken.append(f"重命名 {old_orig_short}  ----->  {new_orig_short}")
                    orig_renamed = True
                    
            old_main_short = main_shape.split('|')[-1]
            new_main_short, m_changed = _safe_rename(main_shape, ideal_main_name)
            if m_changed:
                actions_taken.append(f"重命名 {old_main_short}  ----->  {new_main_short}")
                main_renamed = True
                
            if actions_taken or deleted_nodes:
                rep = {
                    'transform': tr_short, 
                    'status': 'SUCCESS'
                }
                if actions_taken:
                    rep['renames'] = actions_taken
                if deleted_nodes:
                    rep['deleted'] = deleted_nodes
                report_list.append(rep)
    finally:
        cmds.undoInfo(closeChunk=True)
            
    total_renames = sum(len(r.get('renames', [])) for r in report_list)
    total_deleted = sum(len(r.get('deleted', [])) for r in report_list)
    changed_nodes = [
        {
            'transform': r.get('transform', ''),
            'renames': r.get('renames', []),
            'deleted': r.get('deleted', []),
            'status': r.get('status', ''),
        }
        for r in report_list
        if r.get('renames') or r.get('deleted')
    ]
    warnings = [
        {
            'transform': r.get('transform', ''),
            'message': r.get('msg', ''),
        }
        for r in report_list
        if r.get('status') == 'WARNING'
    ]

    items = []
    for r in report_list:
        if r.get('renames') or r.get('deleted'):
            parts = []
            if r.get('renames'):
                parts.append(f'重命名 {len(r["renames"])} 个')
            if r.get('deleted'):
                parts.append(f'删除 {len(r["deleted"])} 个死节点')
            items.append(make_item(
                name=r['transform'],
                detail=', '.join(parts),
            ))

    return make_receipt(
        skill_id='maya_fix_shape_names',
        status='SUCCESS',
        start_time=t0,
        summary_input=scene_name,
        summary_action=f'Shape 规范化 — 重命名 {total_renames} 个, 删除 {total_deleted} 个死节点',
        summary_count=total_renames + total_deleted,
        summary_label='Shape 操作',
        items=items,
        output={
            'target_group': target_grp,
            'renamed_count': total_renames,
            'deleted_dead_shape_count': total_deleted,
            'changed_nodes': changed_nodes,
            'warning_count': len(warnings),
            'warnings': warnings,
        },
    )
