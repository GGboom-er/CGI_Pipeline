# api/operations/check_uvsets/check_uvsets.py
# ── 只读检查 cache 组 mesh 的 UV Set 状况 ──
#
# 扫描 cache 组下所有 mesh，报告每个 mesh 的 UV set 列表、
# 哪些有数据、哪些是空壳。标记需要清理的 mesh。

import time
import maya.cmds as cmds
from core.receipt import make_receipt, make_item


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


def execute(payload):
    t0 = time.time()
    params = payload.get('parameters', {})
    cache_group = params.get('cache_group', '') or 'cache'

    # 定位 cache 组
    if not cmds.objExists(cache_group):
        # 尝试常见变体
        for candidate in ['|Group|cache', '|cache', 'cache']:
            if cmds.objExists(candidate):
                cache_group = candidate
                break
        else:
            return make_receipt('check_uvsets', 'ERROR', t0,
                               error=f'找不到 cache 组: {cache_group}')

    transforms = _get_cache_meshes(cache_group)
    if not transforms:
        return make_receipt('check_uvsets', 'ERROR', t0,
                           error=f'cache 组 "{cache_group}" 下没有 mesh')

    items = []
    need_cleanup = 0
    total_meshes = 0
    problems = []  # 详细问题列表

    for xform in transforms:
        shapes = cmds.listRelatives(xform, shapes=True, fullPath=True,
                                    type='mesh') or []
        # 只检查可见 shape（非 intermediate）
        visible_shapes = [s for s in shapes
                          if not cmds.getAttr(s + '.intermediateObject')]
        if not visible_shapes:
            continue

        total_meshes += 1
        shape = visible_shapes[0]
        short_name = xform.split('|')[-1]

        uv_sets = cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        uv_count = len(uv_sets)

        # 收集每个 UV set 的状态
        uv_details = []
        has_map1 = False
        for uv in uv_sets:
            has_data = _has_uv_data(shape, uv)
            uv_details.append(f'{uv}({"✓" if has_data else "✗"})')
            if uv == 'map1':
                has_map1 = True

        # 判断是否需要清理
        needs_fix = False
        reasons = []
        if uv_count > 1:
            needs_fix = True
            reasons.append(f'{uv_count} 个 UV set')
        if not has_map1 and uv_count > 0:
            needs_fix = True
            reasons.append('缺少 map1')
        if uv_count == 0:
            needs_fix = True
            reasons.append('无 UV set')

        if needs_fix:
            need_cleanup += 1
            problems.append({
                'mesh': short_name,
                'uv_sets': uv_sets,
                'reasons': reasons,
            })

        # 构造明细项
        status_icon = '⚠️' if needs_fix else '✅'
        detail_str = ' · '.join(uv_details) if uv_details else '(无 UV)'
        if reasons:
            detail_str += f' → {", ".join(reasons)}'

        items.append(make_item(
            name=f'{status_icon} {short_name}',
            detail=detail_str,
        ))

    return make_receipt(
        api_id='check_uvsets',
        status='SUCCESS',
        start_time=t0,
        summary_input=cache_group,
        summary_action=f'UV Set 检查 — {need_cleanup}/{total_meshes} 个需要清理',
        summary_count=total_meshes,
        summary_label='mesh',
        items=items,
        outputs={
            'result': {
                'total_meshes': total_meshes,
                'need_cleanup': need_cleanup,
                'problems': problems,
            },
        },
    )
