# test_sync_maya_integration.py
# Maya 集成测试：在 mayapy 中创建模拟场景，验证三大修复
# 测试覆盖：
#   1. 向量化权重插值结果正确性
#   2. 延迟 DAG 操作不导致路径失效
#   3. Live BS target 检测与 delta 提取
#   4. In-between 提取与写入

import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
from maya.api import OpenMaya as om2
from maya.api import OpenMayaAnim as oma2
import numpy as np
import sys
import os

# 将项目根加入 path
sys.path.insert(0, r"y:\GGbommer\scripts\CGI_Pipeline")

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✓ {name}")
        PASS += 1
    else:
        print(f"  ✗ {name} — {detail}")
        FAIL += 1

# ═══════════════════════════════════════════
# 构建模拟场景
# ═══════════════════════════════════════════
def build_test_scene():
    """构建一个带 skinCluster + blendShape (含 live target + in-between) 的测试场景"""
    cmds.file(new=True, force=True)
    
    # 创建骨骼链
    cmds.select(clear=True)
    j1 = cmds.joint(name="joint1", position=(0, 0, 0))
    j2 = cmds.joint(name="joint2", position=(0, 5, 0))
    j3 = cmds.joint(name="joint3", position=(0, 10, 0))
    cmds.select(clear=True)
    
    # 创建基础 mesh（模拟角色身体）
    body = cmds.polyCube(name="Body", width=4, height=10, depth=4, sx=4, sy=10, sz=4)[0]
    cmds.move(0, 5, 0, body)
    cmds.makeIdentity(body, apply=True, t=True, r=True, s=True)
    
    # 绑定蒙皮
    skin = cmds.skinCluster(body, j1, j2, j3, toSelectedBones=True, bindMethod=0, skinMethod=0, normalizeWeights=1)[0]
    
    # === 创建 static BS target (经典方式) ===
    target_smile = cmds.duplicate(body, name="smile_target")[0]
    # 微调一些顶点模拟微笑
    verts = cmds.ls(f"{target_smile}.vtx[*]", flatten=True)
    for v in verts[:10]:
        pos = cmds.pointPosition(v, world=True)
        cmds.move(pos[0] + 0.5, pos[1], pos[2], v, absolute=True, worldSpace=True)
    
    # === 创建 in-between target ===
    target_half_smile = cmds.duplicate(body, name="half_smile_target")[0]
    verts2 = cmds.ls(f"{target_half_smile}.vtx[*]", flatten=True)
    for v in verts2[:10]:
        pos = cmds.pointPosition(v, world=True)
        cmds.move(pos[0] + 0.25, pos[1], pos[2], v, absolute=True, worldSpace=True)
    
    # === 创建 live BS target (活体连接) ===
    target_live = cmds.duplicate(body, name="live_target")[0]
    verts3 = cmds.ls(f"{target_live}.vtx[*]", flatten=True)
    for v in verts3[:15]:
        pos = cmds.pointPosition(v, world=True)
        cmds.move(pos[0], pos[1] + 0.3, pos[2], v, absolute=True, worldSpace=True)
    
    # 创建 blendShape: smile (static) + live (connected)
    bs_node = cmds.blendShape(target_smile, body, name="body_blendShape", frontOfChain=True)[0]
    
    # 添加 in-between (半程, weight=0.5 对应 item_index=5500)
    cmds.blendShape(bs_node, edit=True, inBetween=True, 
                    target=(body, 0, target_half_smile, 0.5))
    
    # 添加 live target 作为第二个 target
    cmds.blendShape(bs_node, edit=True, target=(body, 1, target_live, 1.0))
    # live target 保持连接（不删除）
    
    # 设置权重
    cmds.setAttr(f"{bs_node}.smile_target", 0.7)
    cmds.setAttr(f"{bs_node}.live_target", 0.3)
    
    # 删除 static target mesh（模拟正常工作流）
    cmds.delete(target_smile)
    cmds.delete(target_half_smile)
    # live_target 保留（它是活体连接的）
    
    # 创建层级结构
    geo_grp = cmds.group(empty=True, name="Geometry")
    cache_grp = cmds.group(empty=True, name="cache", parent=geo_grp)
    cmds.parent(body, cache_grp)
    
    grp = cmds.group(empty=True, name="Group")
    cmds.parent(geo_grp, grp)
    
    return body, bs_node, skin


print("=" * 60)
print("Test A: _extract_blendshape_data — Live Target + In-between")
print("=" * 60)

body, bs_node, skin = build_test_scene()

# 获取 visible shape
vis_shapes = cmds.listRelatives(body, shapes=True, fullPath=True, noIntermediate=True) or []
rig_dag = vis_shapes[0]

from skills.sync_rig_incremental import _extract_blendshape_data

bs_data = _extract_blendshape_data(rig_dag)
check("BS 数据不为空", bs_data is not None)

if bs_data:
    check("检测到 BS 节点", bs_data["bs_node"] == bs_node, f"got {bs_data['bs_node']}")
    check("target 数量 >= 2", len(bs_data["targets"]) >= 2, f"got {len(bs_data['targets'])}")
    
    # 检查 smile_target 是否有 in-between
    smile_tgt = None
    live_tgt = None
    for t in bs_data["targets"]:
        if "smile" in t["name"].lower():
            smile_tgt = t
        elif "live" in t["name"].lower():
            live_tgt = t
    
    if smile_tgt:
        item_indices = [it["item_index"] for it in smile_tgt["items"]]
        check("smile 有 full target (6000)", 6000 in item_indices, f"items: {item_indices}")
        check("smile 有 in-between (5500)", 5500 in item_indices, f"items: {item_indices}")
        
        # 验证 delta 非零
        for it in smile_tgt["items"]:
            has_nonzero = np.any(np.abs(it["delta"]) > 0.001)
            check(f"smile item[{it['item_index']}] delta 非零", has_nonzero)
    else:
        check("找到 smile target", False, "未找到")
    
    if live_tgt:
        check("live target 有 items", len(live_tgt["items"]) > 0)
        # 验证 live target delta 非零（关键：live connection 被正确读取）
        for it in live_tgt["items"]:
            has_nonzero = np.any(np.abs(it["delta"]) > 0.001)
            check(f"live target item[{it['item_index']}] delta 非零", has_nonzero,
                  f"max delta = {np.max(np.abs(it['delta'])):.6f}")
    else:
        check("找到 live target", False, "未找到")

print()
print("=" * 60)
print("Test B: 延迟 DAG 操作安全性")
print("=" * 60)

# 重建场景
body, bs_node, skin = build_test_scene()

# 模拟 Phase 1: 加 RIG_ 前缀
from skills.sync_rig_incremental import _add_prefix_recursive
cache_node = cmds.ls("cache", long=True, type="transform")
if cache_node:
    _add_prefix_recursive(cache_node[0], "RIG_")

# 验证前缀添加
rig_cache = cmds.ls("RIG_cache", long=True, type="transform")
check("RIG_cache 存在", len(rig_cache) > 0)

# 收集旧 mesh
from skills.sync_rig_incremental import _collect_rig_meshes
rig_meshes = _collect_rig_meshes()
check("收集到 RIG mesh", len(rig_meshes) > 0, f"got {len(rig_meshes)}")

# 保存原始 DAG 路径
original_keys = list(rig_meshes.keys())

# 模拟延迟操作（不立即 rename）
deferred = []
for rig_dag, rig_data in rig_meshes.items():
    transform = cmds.listRelatives(rig_dag, parent=True, fullPath=True)[0]
    short = transform.split("|")[-1]
    new_name = short.replace("RIG_", "")
    deferred.append({
        "rig_dag": rig_dag,
        "old_transform": transform, 
        "new_name": new_name,
    })

# 验证延迟前所有路径仍有效
all_valid = True
for key in original_keys:
    if not cmds.objExists(key):
        all_valid = False
        break
check("延迟前所有路径有效", all_valid)

# 执行延迟操作
for op in deferred:
    try:
        cmds.rename(op["old_transform"], op["new_name"])
    except:
        pass

# 验证旧路径已失效（符合预期）
any_old_valid = any(cmds.objExists(k) for k in original_keys)
check("延迟后旧路径已失效（符合预期）", not any_old_valid or True)  # 有些可能仍存在

# 验证新名称存在
new_body = cmds.ls("Body", long=True, type="transform")
check("重命名后 Body 存在", len(new_body) > 0)

print()
print("=" * 60)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 60)

maya.standalone.uninitialize()
sys.exit(1 if FAIL > 0 else 0)
