"""
在 Maya 中验证同步后的权重和 BlendShape 变形是否正确。
通过 MCP Maya 执行。
"""

import maya.cmds as cmds
from maya.api import OpenMaya as om2
from maya.api import OpenMayaAnim as oma2
import json

results = []

# 1. 检查 SkinCluster 存在性和权重合法性
cache_node = None
for node in cmds.ls("cache", long=True, type="transform") or []:
    if "|cache" in node or node == "cache":
        cache_node = node
        break

if not cache_node:
    results.append({"test": "find_cache", "status": "FAIL", "detail": "cache node not found"})
else:
    all_meshes = cmds.listRelatives(cache_node, allDescendents=True, type="mesh", fullPath=True) or []
    non_intermediate = [m for m in all_meshes if not cmds.getAttr(m + ".intermediateObject")]

    skin_count = 0
    weight_errors = []

    for shape in non_intermediate:
        history = cmds.listHistory(shape) or []
        skins = cmds.ls(history, type="skinCluster")
        if skins:
            skin_count += 1
            skin_node = skins[0]

            # 检查权重归一化
            sel = om2.MSelectionList()
            sel.add(skin_node)
            fn_skin = oma2.MFnSkinCluster(sel.getDependNode(0))

            shapes_list = cmds.listRelatives(
                cmds.listRelatives(shape, parent=True, fullPath=True)[0],
                shapes=True, fullPath=True, noIntermediate=True
            )
            sel_sh = om2.MSelectionList()
            sel_sh.add(shapes_list[0])
            dag_sh = sel_sh.getDagPath(0)

            num_verts = om2.MFnMesh(dag_sh).numVertices
            comp = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
            om2.MFnSingleIndexedComponent(comp).setCompleteData(num_verts)

            weights, num_inf = fn_skin.getWeights(dag_sh, comp)
            import numpy as np
            w_arr = np.array(weights).reshape(num_verts, num_inf)
            row_sums = w_arr.sum(axis=1)

            bad_rows = np.where(np.abs(row_sums - 1.0) > 0.001)[0]
            if len(bad_rows) > 0:
                short_name = shape.split("|")[-1]
                weight_errors.append(f"{short_name}: {len(bad_rows)} verts unnormalized")

    results.append({
        "test": "skin_clusters",
        "status": "OK" if skin_count > 0 else "WARN",
        "detail": f"{skin_count}/{len(non_intermediate)} meshes have skinCluster"
    })

    results.append({
        "test": "weight_normalization",
        "status": "OK" if not weight_errors else "FAIL",
        "detail": weight_errors if weight_errors else "All weights normalized"
    })

# 2. 检查 BlendShape 存在性和变形效果
bs_nodes = cmds.ls(type="blendShape") or []
bs_results = []
for bs in bs_nodes:
    targets = cmds.listAttr(bs + ".weight", multi=True) or []
    if targets:
        # 测试第一个 target 的变形效果
        first_target = targets[0]
        attr = f"{bs}.{first_target}"

        # 获取关联的 mesh
        geo = cmds.blendShape(bs, query=True, geometry=True)
        if geo:
            # 记录原始位置
            sel = om2.MSelectionList()
            sel.add(geo[0])
            dag = sel.getDagPath(0)
            fn = om2.MFnMesh(dag)
            pts_before = fn.getPoints(om2.MSpace.kWorld)

            # 激活 BS
            old_val = cmds.getAttr(attr)
            cmds.setAttr(attr, 1.0)
            pts_after = fn.getPoints(om2.MSpace.kWorld)
            cmds.setAttr(attr, old_val)

            # 计算变形量
            max_delta = 0.0
            for i in range(len(pts_before)):
                dx = pts_after[i].x - pts_before[i].x
                dy = pts_after[i].y - pts_before[i].y
                dz = pts_after[i].z - pts_before[i].z
                d = (dx*dx + dy*dy + dz*dz) ** 0.5
                if d > max_delta:
                    max_delta = d

            bs_results.append({
                "bs_node": bs,
                "target": first_target,
                "max_delta_cm": round(max_delta, 4),
                "has_effect": max_delta > 0.001
            })

results.append({
    "test": "blendshape_deformation",
    "status": "OK" if all(r["has_effect"] for r in bs_results) else ("WARN" if bs_results else "SKIP"),
    "detail": bs_results[:10]  # 只显示前10个
})

# 3. 验证骨骼变形（移动一根骨骼看 mesh 是否跟随）
joints = cmds.ls(type="joint")
if joints:
    test_joint = None
    for j in joints:
        # 找一个有子骨骼的非根骨骼
        parent = cmds.listRelatives(j, parent=True)
        children = cmds.listRelatives(j, children=True, type="joint")
        if parent and children:
            test_joint = j
            break

    if test_joint:
        # 找受此骨骼影响的 mesh
        affected_skins = cmds.listConnections(test_joint, type="skinCluster") or []
        if affected_skins:
            skin = affected_skins[0]
            geo = cmds.skinCluster(skin, query=True, geometry=True)
            if geo:
                sel = om2.MSelectionList()
                sel.add(geo[0])
                dag = sel.getDagPath(0)
                fn = om2.MFnMesh(dag)
                pts_before = fn.getPoints(om2.MSpace.kWorld)

                # 旋转骨骼
                old_rot = cmds.getAttr(f"{test_joint}.rotateX")
                cmds.setAttr(f"{test_joint}.rotateX", old_rot + 15.0)
                pts_after = fn.getPoints(om2.MSpace.kWorld)
                cmds.setAttr(f"{test_joint}.rotateX", old_rot)

                moved_count = 0
                for i in range(len(pts_before)):
                    dx = pts_after[i].x - pts_before[i].x
                    dy = pts_after[i].y - pts_before[i].y
                    dz = pts_after[i].z - pts_before[i].z
                    if (dx*dx + dy*dy + dz*dz) > 0.0001:
                        moved_count += 1

                results.append({
                    "test": "joint_deformation",
                    "status": "OK" if moved_count > 0 else "FAIL",
                    "detail": f"Joint '{test_joint}' rotation moved {moved_count}/{len(pts_before)} verts on {geo[0].split('|')[-1]}"
                })

print(json.dumps(results, indent=2, ensure_ascii=False))
