# ── 清理绑定体多余 UV Set（数据注入模式）──
#
# 对 cache 组下所有 mesh 执行 UV set 精简：
# 1. 防御拦截（Reference / Lock 节点跳过）
# 2. 快速通道（已是 map1 唯一，仅同步 UI）
# 3. 从目标 shape 自身提取当前激活 UV Set 的几何与 UV 结构 (self_entry)
# 4. 调用管线标准 _regularize_uvset_to_map1 规整 uvSet 名称
# 5. 调用管线标准 _inject_mesh_data_via_plug 将 self_entry 直写注入 cachedInMesh 与持久属性
# 6. 拔除所有 shape 上的幽灵 UV set 名字标签 + 同步 UI

import time
import maya.cmds as cmds
import maya.api.OpenMaya as om2
from cgi_pipeline.core.receipt import make_receipt, make_item


# ─── 辅助函数 ───

def _get_target_shape(obj):
    """获取目标 shape：有绑定取 ShapeOrig，无绑定取可见 shape"""
    shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
    if not shapes:
        return None

    visible_shape = next(
        (s for s in shapes if not cmds.getAttr(s + '.intermediateObject')),
        None
    )
    if not visible_shape:
        return None

    # 尝试通过 deformableShape 获取原始几何
    try:
        orig_plug = cmds.deformableShape(visible_shape, originalGeometry=True)
        if orig_plug:
            return orig_plug[0].split('.')[0]
    except Exception as e:
        cmds.warning(f'获取原始几何失败，回退历史查询: {visible_shape} | {e}')

    # 回退：从历史中找 intermediate mesh
    history = cmds.listHistory(visible_shape, pruneDagObjects=False) or []
    orig_shapes = [
        n for n in history
        if cmds.nodeType(n) == 'mesh'
        and cmds.getAttr(n + '.intermediateObject')
    ]
    return orig_shapes[-1] if orig_shapes else visible_shape


def _strip_ghost_and_sync_ui(obj):
    """拔除幽灵 UV set 名字标签 + 同步 currentUVSet 为 map1"""
    for s in cmds.listRelatives(obj, shapes=True, fullPath=True) or []:
        indices = cmds.getAttr(s + '.uvSet', multiIndices=True) or []
        for i in indices:
            attr_path = '{}.uvSet[{}]'.format(s, i)
            try:
                if cmds.getAttr(attr_path + '.uvSetName') != 'map1':
                    cmds.removeMultiInstance(attr_path, b=True)
            except Exception as e:
                cmds.warning(f'移除幽灵 UV Set 失败: {attr_path} | {e}')

        # 同步 UI：确保 currentUVSet 属性和 polyUVSet 都指向 map1
        try:
            cmds.setAttr(s + '.currentUVSet', 'map1', type='string')
        except Exception as e:
            cmds.warning(f'设置 currentUVSet 失败: {s} | {e}')
        try:
            cmds.polyUVSet(s, currentUVSet=True, uvSet='map1')
        except Exception as e:
            cmds.warning(f'同步 polyUVSet 失败: {s} | {e}')


def _get_cache_meshes(cache_group):
    """获取 cache 组下所有 mesh transform"""
    if not cmds.objExists(cache_group):
        return []
    descendants = cmds.listRelatives(cache_group, allDescendents=True,
                                     type='mesh', fullPath=True) or []
    transforms = set()
    for mesh_shape in descendants:
        parent = cmds.listRelatives(mesh_shape, parent=True, fullPath=True)
        if parent:
            transforms.add(parent[0])
    return sorted(transforms)


def _extract_self_mesh_entry(shape):
    """从 Shape/ShapeOrig 自身使用 OpenMaya 2.0 提取基础几何与当前激活 UV Set 数据"""
    sl = om2.MSelectionList()
    sl.add(shape)
    dag = sl.getDagPath(0)
    if dag.apiType() != om2.MFn.kMesh:
        dag.extendToShape()

    fn_mesh = om2.MFnMesh(dag)
    pts = fn_mesh.getPoints(om2.MSpace.kObject)
    vp = []
    for p in pts:
        vp.extend([p.x, p.y, p.z])

    counts, conn = fn_mesh.getUnassignedVertices()  # 获取面顶点分布
    # 正确使用 getPolygonVertices 逐面提取拓扑
    fc = []
    fi = []
    for i in range(fn_mesh.numPolygons):
        verts = fn_mesh.getPolygonVertices(i)
        fc.append(len(verts))
        fi.extend(verts)

    uv_sets = fn_mesh.getUVSetNames()
    if not uv_sets:
        return None

    current_set = fn_mesh.currentUVSetName()
    u, v = fn_mesh.getUVs(current_set)

    # 提取当前 Set 的面-顶点 UV 分配
    uv_counts, uvi = fn_mesh.getAssignedUVs(current_set)

    # 若当前 Set 为空，寻找第一个包含数据的 Set
    if len(u) == 0:
        for s in uv_sets:
            u_tmp, v_tmp = fn_mesh.getUVs(s)
            if len(u_tmp) > 0:
                current_set = s
                u, v = u_tmp, v_tmp
                uv_counts, uvi = fn_mesh.getAssignedUVs(s)
                break

    return {
        "vert_positions": vp,
        "face_counts": fc,
        "face_indices": fi,
        "u_array": list(u),
        "v_array": list(v),
        "uv_indices": list(uvi),
        "source_uvset": current_set
    }


def _process_one_mesh(xform, target_shape):
    """对单个 mesh 按照管线标准注入协议执行：提取自身 -> 规整名称 -> Plug 数据直写注入 -> 清理 pnts -> 清理 UI"""
    # 从底层引用管线公共注入函数与 pnts 清理函数
    from cgi_pipeline.capabilities.maya.rig.maya_sync_rig_incremental.maya_sync_rig_incremental import (
        _regularize_uvset_to_map1,
        _inject_mesh_data_via_plug,
        _clear_pnts_tweak
    )

    # 1. 提取自身数据
    entry = _extract_self_mesh_entry(target_shape)
    if not entry:
        return

    # 2. 规整 uvSet 名称（真 shape 上将保留项重命名/收敛为唯一 map1）
    _regularize_uvset_to_map1(target_shape)

    # 3. 内存构建 MFnMeshData 并注入 cachedInMesh Plug 与持久属性
    _inject_mesh_data_via_plug(target_shape, entry)

    # 4. 清理 visible_shape 和 target_shape (ShapeOrig) 上的 .pnts 顶点位移残留
    shapes = cmds.listRelatives(xform, shapes=True, fullPath=True) or []
    visible_shape = next((s for s in shapes if not cmds.getAttr(s + '.intermediateObject')), None)
    if visible_shape:
        _clear_pnts_tweak(visible_shape)
    if target_shape and target_shape != visible_shape:
        _clear_pnts_tweak(target_shape)

    # 5. 清理幽灵标签与同步 UI
    _strip_ghost_and_sync_ui(xform)



# ─── 主函数 ───

def execute(payload):
    t0 = time.time()
    params = payload.get('parameters', {})
    cache_group = params.get('cache_group', '') or 'cache'

    # 定位 cache 组
    if not cmds.objExists(cache_group):
        for candidate in ['|Group|cache', '|cache', 'cache']:
            if cmds.objExists(candidate):
                cache_group = candidate
                break
        else:
            return make_receipt('simplify_uvsets', 'ERROR', t0,
                               error=f'找不到 cache 组: {cache_group}')

    transforms = _get_cache_meshes(cache_group)
    if not transforms:
        return make_receipt('simplify_uvsets', 'ERROR', t0,
                           error=f'cache 组 "{cache_group}" 下没有 mesh')

    items = []
    cleaned = 0
    skipped = 0
    fast_passed = 0
    errors = []

    # 包裹在撤销块中
    cmds.undoInfo(openChunk=True, chunkName='simplify_uvsets')
    try:
        for xform in transforms:
            short_name = xform.split('|')[-1]
            target_shape = _get_target_shape(xform)
            if not target_shape:
                skipped += 1
                items.append(make_item(name=f'⏭ {short_name}',
                                       detail='无可用 shape，已跳过'))
                continue

            # ── 防御：引用资产拦截 ──
            try:
                if cmds.referenceQuery(target_shape, isNodeReferenced=True):
                    skipped += 1
                    items.append(make_item(name=f'🔒 {short_name}',
                                           detail='引用资产，无法修改'))
                    continue
            except Exception as e:
                items.append(make_item(name=f'⚠ {short_name}',
                                       detail=f'referenceQuery 失败，按非引用继续: {e}'))

            # ── 防御：锁定节点拦截 ──
            try:
                if cmds.lockNode(target_shape, q=True, lock=True)[0]:
                    skipped += 1
                    items.append(make_item(name=f'🔒 {short_name}',
                                           detail='节点已锁定，无法修改'))
                    continue
            except Exception as e:
                items.append(make_item(name=f'⚠ {short_name}',
                                       detail=f'锁定状态查询失败，按未锁定继续: {e}'))

            # ── 快速通道：已是 map1 唯一 ──
            uv_sets = cmds.polyUVSet(target_shape, query=True,
                                     allUVSets=True) or []
            if len(uv_sets) == 1 and uv_sets[0] == 'map1':
                _strip_ghost_and_sync_ui(xform)
                fast_passed += 1
                items.append(make_item(name=f'✅ {short_name}',
                                       detail='已是 map1 唯一，已同步 UI'))
                continue

            # ── 重度注入清理 ──
            try:
                _process_one_mesh(xform, target_shape)
                cleaned += 1
                items.append(make_item(name=f'🔧 {short_name}',
                                       detail='UV set 已注入规整为 map1'))
            except Exception as e:
                errors.append(short_name)
                items.append(make_item(name=f'❌ {short_name}',
                                       detail=str(e)[:80]))
    finally:
        cmds.undoInfo(closeChunk=True)

    action = f'UV Set 精简 — {cleaned} 个已清理'
    if fast_passed:
        action += f'，{fast_passed} 个快速通过'
    if skipped:
        action += f'，{skipped} 个跳过'
    if errors:
        action += f'，{len(errors)} 个失败'

    return make_receipt(
        api_id='simplify_uvsets',
        status='SUCCESS',
        start_time=t0,
        summary_input=cache_group,
        summary_action=action,
        summary_count=cleaned + fast_passed,
        summary_label='mesh',
        items=items,
        output={
            'result': {
                'cleaned': cleaned,
                'fast_passed': fast_passed,
                'skipped': skipped,
                'errors': errors,
            },
        },
    )
