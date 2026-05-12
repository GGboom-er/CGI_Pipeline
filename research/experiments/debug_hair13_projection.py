"""
调试 hair13 投射关系：
- 找到 hair13 每个顶点在 SuperMesh 中命中了哪个源 mesh
- 用 locator 标记出来
- 显示重心坐标权重（投射权重，非蒙皮权重）
"""
import maya.cmds as cmds
import maya.api.OpenMaya as om2
import numpy as np
import json


def _closest_face_bruteforce(face_v0, face_v1, face_v2, query_pts):
    """
    纯 numpy 暴力最近面质心查询
    face_v0/v1/v2: (F, 3)
    query_pts: (N, 3)
    返回: dists (N,), face_ids (N,)
    """
    centroids = (face_v0 + face_v1 + face_v2) / 3.0  # (F, 3)
    # 分批计算避免内存爆炸
    n = len(query_pts)
    f = len(centroids)
    batch = 50
    dists = np.empty(n)
    fids = np.empty(n, dtype=np.int32)

    for i in range(0, n, batch):
        end = min(i + batch, n)
        # (batch, 1, 3) - (1, F, 3) -> (batch, F, 3)
        diff = query_pts[i:end, None, :] - centroids[None, :, :]
        d2 = np.sum(diff * diff, axis=2)  # (batch, F)
        best = np.argmin(d2, axis=1)
        fids[i:end] = best
        dists[i:end] = np.sqrt(d2[np.arange(end-i), best])

    return dists, fids


def run(max_locs=30):
    # 1. 读取目标 mesh (hair13)
    target_shape = '|Group|Geometry|cache|mihouwang_hair_Grp|mihouwang_hair13|mihouwang_hair13Shape'
    sel = om2.MSelectionList()
    sel.add(target_shape)
    dag = sel.getDagPath(0)
    fn = om2.MFnMesh(dag)
    pts = fn.getPoints(om2.MSpace.kWorld)
    target_verts = np.array([[p.x, p.y, p.z] for p in pts])
    num_verts = len(target_verts)

    # 2. 构建 SuperMesh
    rig_shapes = [
        '|Group|Geometry|RIG_cache|RIG_mihouwang_hairbasemesh_Grp|RIG_mihouwang_hair17_hairbasemesh|RIG_mihouwang_hair17_hairbasemeshShape',
        '|Group|Geometry|RIG_cache|RIG_mihouwang_hairbasemesh_Grp|RIG_mihouwang_hair16_hairbasemesh|RIG_mihouwang_hair16_hairbasemeshShape',
        '|Group|Geometry|RIG_cache|RIG_mihouwang_hairbasemesh_Grp|RIG_mihouwang_hair46_hairbasemesh|RIG_mihouwang_hair46_hairbasemeshShape',
        '|Group|Geometry|RIG_cache|RIG_mihouwang_hairbasemesh_Grp|RIG_mihouwang_hair23_hairbasemesh|RIG_mihouwang_hair23_hairbasemeshShape',
        '|Group|Geometry|RIG_cache|RIG_mihouwang_hairbasemesh_Grp|RIG_mihouwang_hair42_hairbasemesh|RIG_mihouwang_hair42_hairbasemeshShape',
        '|Group|Geometry|RIG_cache|RIG_mihouwang_hairbasemesh_Grp|RIG_mihouwang_hair7_hairbasemesh|RIG_mihouwang_hair7_hairbasemeshShape',
        '|Group|Geometry|RIG_cache|RIG_mihouwang_hairbasemesh_Grp|RIG_mihouwang_hair22_hairbasemesh|RIG_mihouwang_hair22_hairbasemeshShape',
        '|Group|Geometry|RIG_cache|RIG_mihouwang_hairbasemesh_Grp|RIG_mihouwang_hair15_hairbasemesh|RIG_mihouwang_hair15_hairbasemeshShape',
        '|Group|Geometry|RIG_cache|RIG_mihouwang_cloth_Grp|RIG_mihouwang_clothes_Grp|RIG_mihouwang_clothes3|RIG_mihouwang_clothes3Shape',
    ]
    rig_names = [s.split('|')[-1].replace('RIG_','').replace('Shape','') for s in rig_shapes]

    sv_list = []
    sf_list = []
    fsrc_list = []
    voff = 0

    for si, shape in enumerate(rig_shapes):
        s = om2.MSelectionList()
        s.add(shape)
        d = s.getDagPath(0)
        f = om2.MFnMesh(d)
        p = f.getPoints(om2.MSpace.kWorld)
        v = np.array([[pt.x, pt.y, pt.z] for pt in p])
        fc, fi = f.getVertices()
        idx = 0
        for cnt in fc:
            for t in range(cnt - 2):
                sf_list.append([fi[idx]+voff, fi[idx+t+1]+voff, fi[idx+t+2]+voff])
                fsrc_list.append(si)
            idx += cnt
        sv_list.append(v)
        voff += len(v)

    sv = np.vstack(sv_list)
    sf = np.array(sf_list, dtype=np.int32)
    fsa = np.array(fsrc_list, dtype=np.int32)

    # 3. 查询最近面
    fv0 = sv[sf[:, 0]]
    fv1 = sv[sf[:, 1]]
    fv2 = sv[sf[:, 2]]
    dists, fids = _closest_face_bruteforce(fv0, fv1, fv2, target_verts)
    hit_sources = fsa[fids]

    # 4. 计算重心坐标
    v0 = fv0[fids]
    v1 = fv1[fids]
    v2 = fv2[fids]

    e0 = v1 - v0
    e1 = v2 - v0
    vp = target_verts - v0

    d00 = np.sum(e0 * e0, axis=1)
    d01 = np.sum(e0 * e1, axis=1)
    d11 = np.sum(e1 * e1, axis=1)
    dp0 = np.sum(vp * e0, axis=1)
    dp1 = np.sum(vp * e1, axis=1)

    denom = d00 * d11 - d01 * d01
    denom[denom == 0] = 1e-10

    bary_v = (d11 * dp0 - d01 * dp1) / denom
    bary_w = (d00 * dp1 - d01 * dp0) / denom
    bary_u = 1.0 - bary_v - bary_w
    bary = np.column_stack([bary_u, bary_v, bary_w])

    # 投影点
    proj_pts = bary_u.reshape(-1,1)*v0 + bary_v.reshape(-1,1)*v1 + bary_w.reshape(-1,1)*v2

    # 5. 统计
    counts = {}
    for i in range(len(rig_names)):
        c = int((hit_sources == i).sum())
        if c > 0:
            counts[rig_names[i]] = c

    # 6. 清理旧 debug locators
    old_grp = 'DEBUG_hair13_projection_grp'
    if cmds.objExists(old_grp):
        cmds.delete(old_grp)
    grp = cmds.group(empty=True, name=old_grp)

    # 7. 创建 locator 可视化
    step = max(1, num_verts // max_locs)
    sample_ids = list(range(0, num_verts, step))[:max_locs]

    colors = [
        (1, 0, 0),      # hair17 - red
        (0, 1, 0),      # hair16 - green
        (0, 0, 1),      # hair46 - blue
        (1, 1, 0),      # hair23 - yellow
        (1, 0, 1),      # hair42 - magenta
        (0, 1, 1),      # hair7 - cyan
        (1, 0.5, 0),    # hair22 - orange
        (0.5, 0, 1),    # hair15 - purple
        (0.5, 0.5, 0.5),# clothes3 - gray
    ]

    annotations = []
    for vi in sample_ids:
        src_id = int(hit_sources[vi])
        src_name = rig_names[src_id]
        dist = float(dists[vi])
        b = bary[vi]

        # 目标点 locator（白色小）
        loc_tgt = cmds.spaceLocator(name='hair13_v{}_target'.format(vi))[0]
        cmds.setAttr(loc_tgt + '.translate', float(target_verts[vi][0]), float(target_verts[vi][1]), float(target_verts[vi][2]))
        cmds.setAttr(loc_tgt + '.localScaleX', 0.1)
        cmds.setAttr(loc_tgt + '.localScaleY', 0.1)
        cmds.setAttr(loc_tgt + '.localScaleZ', 0.1)
        cmds.setAttr(loc_tgt + '.overrideEnabled', 1)
        cmds.setAttr(loc_tgt + '.overrideColor', 16)

        # 源投影点 locator（彩色大）
        loc_src = cmds.spaceLocator(name='hair13_v{}_{}'.format(vi, src_name))[0]
        pp = proj_pts[vi]
        cmds.setAttr(loc_src + '.translate', float(pp[0]), float(pp[1]), float(pp[2]))
        cmds.setAttr(loc_src + '.localScaleX', 0.2)
        cmds.setAttr(loc_src + '.localScaleY', 0.2)
        cmds.setAttr(loc_src + '.localScaleZ', 0.2)
        cmds.setAttr(loc_src + '.overrideEnabled', 1)
        cmds.setAttr(loc_src + '.overrideRGBColors', 1)
        col = colors[src_id % len(colors)]
        cmds.setAttr(loc_src + '.overrideColorR', col[0])
        cmds.setAttr(loc_src + '.overrideColorG', col[1])
        cmds.setAttr(loc_src + '.overrideColorB', col[2])

        cmds.parent(loc_tgt, grp)
        cmds.parent(loc_src, grp)

        annotations.append({
            'vtx': vi,
            'source': src_name,
            'dist': round(dist, 5),
            'bary': [round(float(b[0]),3), round(float(b[1]),3), round(float(b[2]),3)],
        })

    # 8. 输出结果
    output = {
        'target': 'mihouwang_hair13',
        'num_verts': num_verts,
        'source_hit_counts': counts,
        'sample_annotations': annotations,
        'locator_group': old_grp,
        'color_legend': {rig_names[i]: list(colors[i]) for i in range(len(rig_names))},
    }

    out_path = r'Y:\GGbommer\scripts\CGI_Pipeline\debug_hair13_result.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return output


if __name__ == '__main__':
    run()
