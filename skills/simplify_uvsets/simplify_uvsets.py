# skills/simplify_uvsets.py
# ── 清理绑定体多余 UV Set（沙盒模式）──
#
# 对 cache 组下所有 mesh 执行 UV set 精简：
# 1. 防御拦截（Reference / Lock 节点跳过）
# 2. 快速通道（已是 map1 唯一，仅同步 UI）
# 3. 通过 ShapeOrig（绑定源几何）提取到临时 mesh（沙盒）
# 4. 在沙盒内找到最佳 UV set，覆盖到 index 0，删除其余
# 5. 统一命名为 map1
# 6. 数据回灌到 ShapeOrig
# 7. 拔除所有 shape 上的幽灵 UV set 名字标签 + 同步 UI
#
# ⚠️ 破坏性操作，建议先用 check_uvsets 查看再执行

import time
import maya.cmds as cmds
from core.receipt import make_receipt, make_item


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


def _has_uv_data(shape, uvset):
    """安全快速验证指定 UV set 是否有实际数据"""
    try:
        current = cmds.polyUVSet(shape, q=True, currentUVSet=True)[0]
        cmds.polyUVSet(shape, currentUVSet=True, uvSet=uvset)
        count = cmds.polyEvaluate(shape, uvcoord=True)
        cmds.polyUVSet(shape, currentUVSet=True, uvSet=current)
        return count > 0
    except Exception:
        return False


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

            # ── 重度清理 ──
            try:
                _process_one_mesh(xform, target_shape)
                cleaned += 1
                items.append(make_item(name=f'🔧 {short_name}',
                                       detail='UV set 已精简为 map1'))
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
        skill_id='simplify_uvsets',
        status='SUCCESS',
        start_time=t0,
        summary_input=cache_group,
        summary_action=action,
        summary_count=cleaned + fast_passed,
        summary_label='mesh',
        items=items,
        outputs={
            'cleaned': cleaned,
            'fast_passed': fast_passed,
            'skipped': skipped,
            'errors': errors,
        },
    )


def _process_one_mesh(xform, target_shape):
    """对单个 mesh 执行沙盒 UV 清理 + 回灌"""

    # ── 1. 创建沙盒 ──
    temp_shape = cmds.createNode('mesh')
    temp_transform = cmds.listRelatives(temp_shape, parent=True,
                                        fullPath=True)[0]

    # try...finally 确保沙盒绝对不遗留
    try:
        cmds.connectAttr(target_shape + '.outMesh',
                         temp_shape + '.inMesh', force=True)
        cmds.getAttr(temp_shape + '.boundingBoxMin')  # 强制求值
        cmds.disconnectAttr(target_shape + '.outMesh',
                            temp_shape + '.inMesh')

        # ── 2. 沙盒内 UV 清理 ──
        temp_uv_sets = cmds.polyUVSet(temp_shape, query=True,
                                       allUVSets=True) or []
        if temp_uv_sets:
            default_uv = temp_uv_sets[0]  # Index 0 永远不可删除

            # A. 找最佳 UV set（优先 map1，其次第一个有数据的）
            if 'map1' in temp_uv_sets and _has_uv_data(temp_shape, 'map1'):
                best_uv = 'map1'
            else:
                best_uv = next(
                    (uv for uv in temp_uv_sets if _has_uv_data(temp_shape, uv)),
                    None
                )

            # B. 如果最佳不在 index 0，覆盖过去
            if best_uv and best_uv != default_uv:
                cmds.polyUVSet(temp_shape, currentUVSet=True, uvSet=default_uv)
                cmds.polyCopyUV(temp_shape, uvSetNameInput=best_uv,
                                uvSetName=default_uv, ch=False)

            # C. 删除 index 0 以外的所有 UV set
            cmds.polyUVSet(temp_shape, currentUVSet=True, uvSet=default_uv)
            for uv in temp_uv_sets[1:]:
                try:
                    cmds.polyUVSet(temp_shape, delete=True, uvSet=uv)
                except Exception as e:
                    cmds.warning(f'删除临时 UV Set 失败: {temp_shape}.{uv} | {e}')

            # D. 重命名 index 0 为 map1
            if default_uv != 'map1':
                try:
                    cmds.polyUVSet(temp_shape, rename=True,
                                   uvSet=default_uv, newUVSet='map1')
                except Exception as e:
                    cmds.warning(f'重命名临时 UV Set 失败: {temp_shape}.{default_uv} -> map1 | {e}')

        cmds.delete(temp_transform, ch=True)  # 清空沙盒历史

        # ── 3. 数据回灌 ──
        cmds.connectAttr(temp_shape + '.outMesh',
                         target_shape + '.inMesh', force=True)
        cmds.getAttr(target_shape + '.boundingBoxMin')  # 强制求值
        cmds.disconnectAttr(temp_shape + '.outMesh',
                            target_shape + '.inMesh')

        # ── 4. 拔除幽灵 + 同步 UI ──
        _strip_ghost_and_sync_ui(xform)

    finally:
        # 无论发生什么，确保干掉沙盒节点
        if cmds.objExists(temp_transform):
            cmds.delete(temp_transform)
