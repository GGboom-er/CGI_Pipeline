# api/operations/maya_fix_shape_names/maya_fix_shape_names.py
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


def _rewire_junk_orig_to_true(junk_shape, true_orig):
    """把垃圾 orig 的下游消费者(变形器 inputGeometry 等)改接到真 orig，完成统一。

    只有拓扑(点数)与真 orig 一致才改——一致=同一几何的过时副本，安全统一；
    不一致=真混用(不同几何各喂不同变形器)，判冲突、不动、交人工。
    返回 (ok, reason)：ok=True 已改接可删；ok=False 未动(冲突/失败)。
    """
    try:
        junk_vtx = cmds.polyEvaluate(junk_shape, vertex=True)
        true_vtx = cmds.polyEvaluate(true_orig, vertex=True)
    except Exception as e:
        return False, f"点数查询失败({e})"
    if not isinstance(junk_vtx, int) or junk_vtx != true_vtx:
        return False, f"点数不一致({junk_vtx} vs {true_vtx})"
    # [srcPlug, destPlug, ...]：srcPlug 在垃圾 orig 上(如 .worldMesh[0]/.outMesh)，destPlug 是消费者
    conns = cmds.listConnections(
        junk_shape, source=False, destination=True, plugs=True, connections=True
    ) or []
    for i in range(0, len(conns), 2):
        src_plug = conns[i]
        dst_plug = conns[i + 1]
        attr = src_plug.split('.', 1)[1] if '.' in src_plug else 'outMesh'
        new_src = f"{true_orig}.{attr}"
        try:
            cmds.disconnectAttr(src_plug, dst_plug)
            cmds.connectAttr(new_src, dst_plug, force=True)
        except Exception as e:
            return False, f"改接失败({e})"
    return True, "已改接"


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

            # 权威定 THE 真 orig（deformableShape 图关系，不靠"有没有连接"猜）
            from dccs.maya.asset_info_collector import get_shape_orig
            true_orig = get_shape_orig(main_shape, tr)
            true_orig_long = (cmds.ls(true_orig, long=True) or [true_orig])[0] if true_orig else None

            actions_taken = []
            deleted_nodes = []

            # 清理其余 intermediate：真 orig 保留；死残壳(无连接)删；
            # 垃圾 orig(有连接但非真 orig)→ 改接消费者到真 orig，拓扑一致才删、不一致标冲突不动。
            for sh in intermediate_shapes:
                sh_long = (cmds.ls(sh, long=True) or [sh])[0]
                if true_orig_long and sh_long == true_orig_long:
                    continue  # 真 orig，保留
                sh_short = sh.split('|')[-1]
                try:
                    if cmds.lockNode(sh, q=True, lock=True)[0]:
                        cmds.lockNode(sh, lock=False)
                except Exception:
                    pass
                outs = cmds.listConnections(sh, source=False, destination=True) or []
                if not outs:
                    # 死残壳：直接删
                    try:
                        cmds.delete(sh)
                        deleted_nodes.append(sh_short)
                    except Exception as e:
                        report_list.append({'transform': tr_short, 'status': 'WARNING', 'msg': f'删除死节点失败: {sh}: {e}'})
                    continue
                # 有连接的垃圾 orig
                if not true_orig_long:
                    report_list.append({'transform': tr_short, 'status': 'WARNING', 'msg': f'垃圾 orig {sh_short} 有连接但无法确定真 orig（可能重影/多可见 shape），跳过不动。'})
                    continue
                ok, why = _rewire_junk_orig_to_true(sh, true_orig_long)
                if ok:
                    try:
                        cmds.delete(sh)
                        deleted_nodes.append(sh_short)
                        actions_taken.append(f"垃圾 orig {sh_short} 改接真 orig 并删除")
                    except Exception as e:
                        report_list.append({'transform': tr_short, 'status': 'WARNING', 'msg': f'垃圾 orig 改接后删除失败: {sh}: {e}'})
                else:
                    report_list.append({'transform': tr_short, 'status': 'WARNING', 'msg': f'垃圾 orig {sh_short} 与真 orig {why}，判为混用冲突，标记不动、交人工。'})

            # 要改名的 orig（权威版）
            actual_orig_to_rename = true_orig_long
                
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
        api_id='maya_fix_shape_names',
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
