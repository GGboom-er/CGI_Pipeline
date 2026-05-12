# skills/compare_mesh_topology.py
# ── Mesh 拓扑对比 ──

import maya.cmds as cmds
import maya.api.OpenMaya as om2
import os
import sys
import time

from core.bootstrap import PROJECT_ROOT as _PROJECT_ROOT

from core.receipt import make_receipt, make_item


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def _strip_namespace(name):
    """去掉命名空间，返回短名"""
    return name.split(":")[-1]


def _get_shape(node):
    """获取 transform 下的第一个非中间物 shape"""
    shapes = cmds.listRelatives(
        node, children=True, shapes=True, ni=True, fullPath=True
    ) or []
    return shapes[0] if shapes else None


def _get_transform(shape):
    """从 shape 拿 transform"""
    parents = cmds.listRelatives(shape, parent=True, fullPath=False) or []
    return parents[0] if parents else shape


def _compare_vertex_positions(shape_a, shape_b, threshold=1e-4):
    """
    用 OpenMaya 比较两个 shape 的世界空间顶点位置。

    返回:
        (diff_rate, min_diff, max_diff) — 正常
        (None, count_a, count_b)        — 顶点数不一致
    """
    sel = om2.MSelectionList()
    sel.add(shape_a)
    sel.add(shape_b)
    path_a, path_b = sel.getDagPath(0), sel.getDagPath(1)
    fn_a, fn_b = om2.MFnMesh(path_a), om2.MFnMesh(path_b)
    pts_a = fn_a.getPoints(om2.MSpace.kWorld)
    pts_b = fn_b.getPoints(om2.MSpace.kWorld)

    if len(pts_a) != len(pts_b):
        return None, len(pts_a), len(pts_b)

    diffs = [
        p.distanceTo(q)
        for p, q in zip(pts_a, pts_b)
        if p.distanceTo(q) > threshold
    ]
    rate = float(len(diffs)) / len(pts_a) if pts_a else 0.0

    if diffs:
        return rate, min(diffs), max(diffs)
    return rate, 0.0, 0.0


# ═══════════════════════════════════════════
# 层级匹配
# ═══════════════════════════════════════════

def _match_recursive(ref_grp, tgt_grp, lookup, result):
    """递归配对参考组和目标组下的子节点"""
    refs = cmds.listRelatives(ref_grp, children=True, type="transform") or []
    tgts = cmds.listRelatives(tgt_grp, children=True, type="transform") or []
    matched_tgts = set()

    for r in refs:
        key = _strip_namespace(r)
        if key in lookup:
            t = lookup[key]
            matched_tgts.add(t)

            # shape 配对
            rs, ts = _get_shape(r), _get_shape(t)
            if rs and ts:
                rs_short = rs.split("|")[-1]
                ts_short = ts.split("|")[-1]
                if ts_short in rs_short:
                    matched_tgts.add(ts)
                    # 检查 Orig 节点（绑定体变形前的参考拓扑）
                    orig = ts + "Orig"
                    if cmds.objExists(orig):
                        diff = _compare_vertex_positions(rs, orig)
                        if diff[0] is None or diff[0] > 0:
                            result["diffs"].append({
                                "ref_shape": rs,
                                "tgt_shape": orig,
                                "ref_name": _strip_namespace(_get_transform(rs)),
                                "tgt_name": _strip_namespace(
                                    _get_transform(orig.replace("Orig", ""))
                                ),
                                "diff_rate": diff[0],
                                "min_diff": diff[1],
                                "max_diff": diff[2],
                                "type": "count_mismatch" if diff[0] is None else "position_diff",
                            })
                else:
                    result["ref_unmatched"].append(rs_short)
                    result["tgt_unmatched"].append(ts_short)

            _match_recursive(r, t, lookup, result)
        else:
            result["ref_unmatched"].append(r)

    for t in tgts:
        if t not in matched_tgts:
            result["tgt_unmatched"].append(t)


def _match_hierarchy(ref_grp, tgt_grp):
    """顶层匹配入口"""
    all_tgts = cmds.listRelatives(
        tgt_grp, allDescendents=True, type="transform"
    ) or []
    lookup = {_strip_namespace(n): n for n in all_tgts}

    result = {
        "ref_unmatched": [],
        "tgt_unmatched": [],
        "diffs": [],
    }
    _match_recursive(ref_grp, tgt_grp, lookup, result)
    return result


# ═══════════════════════════════════════════
# 技能入口
# ═══════════════════════════════════════════

def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    ref_group = params.get('ref_group', '')
    tgt_group = params.get('tgt_group', '')
    threshold = params.get('threshold', 1e-4)

    if not ref_group or not tgt_group:
        return make_receipt('maya_compare_mesh_topology', 'ERROR', t0,
                            error='缺少必填参数: ref_group 和 tgt_group')

    if not cmds.objExists(ref_group):
        return make_receipt('maya_compare_mesh_topology', 'ERROR', t0,
                            error=f'参考组不存在: {ref_group}')
    if not cmds.objExists(tgt_group):
        return make_receipt('maya_compare_mesh_topology', 'ERROR', t0,
                            error=f'目标组不存在: {tgt_group}')

    # 执行对比
    result = _match_hierarchy(ref_group, tgt_group)

    # 分类差异
    count_mismatches = [d for d in result["diffs"] if d["type"] == "count_mismatch"]
    pos_diffs = [d for d in result["diffs"] if d["type"] == "position_diff"]
    pos_diffs.sort(key=lambda x: x["diff_rate"], reverse=True)

    # 汇总
    summary = {
        "ref_unmatched_count": len(result["ref_unmatched"]),
        "tgt_unmatched_count": len(result["tgt_unmatched"]),
        "count_mismatch_count": len(count_mismatches),
        "position_diff_count": len(pos_diffs),
        "total_issues": (
            len(result["ref_unmatched"]) +
            len(result["tgt_unmatched"]) +
            len(count_mismatches) +
            len(pos_diffs)
        ),
    }

    # 格式化 diff 数据（确保 JSON 可序列化）
    formatted_diffs = []
    for d in count_mismatches + pos_diffs:
        fd = {
            "ref_name": d["ref_name"],
            "tgt_name": d["tgt_name"],
            "type": d["type"],
        }
        if d["type"] == "count_mismatch":
            fd["ref_vertex_count"] = d["min_diff"]  # min_diff = count_a
            fd["tgt_vertex_count"] = d["max_diff"]  # max_diff = count_b
        else:
            fd["diff_rate"] = round(d["diff_rate"], 6)
            fd["diff_rate_pct"] = f"{d['diff_rate']:.4%}"
            fd["min_diff"] = round(d["min_diff"], 6)
            fd["max_diff"] = round(d["max_diff"], 6)
        formatted_diffs.append(fd)

    has_issues = summary["total_issues"] > 0
    status_msg = (
        f"发现 {summary['total_issues']} 个问题"
        if has_issues else "拓扑完全一致，无差异"
    )

    items = []
    for d in formatted_diffs:
        if d["type"] == "count_mismatch":
            items.append(make_item(
                name=d["ref_name"],
                detail=f'顶点数不一致: ref={d["ref_vertex_count"]} vs tgt={d["tgt_vertex_count"]}',
            ))
        else:
            items.append(make_item(
                name=d["ref_name"],
                detail=f'位置差异 {d["diff_rate_pct"]} (max={d["max_diff"]:.4f})',
            ))
    for name in result["ref_unmatched"]:
        items.append(make_item(name=name, detail='参考侧未匹配'))
    for name in result["tgt_unmatched"]:
        items.append(make_item(name=name, detail='目标侧未匹配'))

    receipt = make_receipt(
        skill_id='maya_compare_mesh_topology',
        status='SUCCESS',
        start_time=t0,
        summary_input=f'{ref_group} vs {tgt_group}',
        summary_action=status_msg,
        summary_count=summary["total_issues"],
        summary_label='问题',
        items=items,
    )
    receipt['_legacy'] = {
        'summary': summary,
        'ref_unmatched': result["ref_unmatched"],
        'tgt_unmatched': result["tgt_unmatched"],
        'diffs': formatted_diffs,
    }
    return receipt
