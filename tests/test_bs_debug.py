# test_bs_debug.py — 快速诊断 BS target 名称
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import sys
sys.path.insert(0, r"y:\GGbommer\scripts\CGI_Pipeline")

cmds.file(new=True, force=True)

# 创建骨骼和 mesh
cmds.select(clear=True)
cmds.joint(name="j1", p=(0,0,0))
cmds.joint(name="j2", p=(0,5,0))
cmds.select(clear=True)

body = cmds.polyCube(name="Body", w=4, h=10, d=4, sx=2, sy=5, sz=2)[0]
cmds.move(0,5,0,body); cmds.makeIdentity(body, apply=True, t=True)

# static target
t1 = cmds.duplicate(body, name="smile_target")[0]
verts = cmds.ls(f"{t1}.vtx[0:5]", flatten=True)
for v in verts:
    p = cmds.pointPosition(v, w=True)
    cmds.move(p[0]+0.5, p[1], p[2], v, a=True, ws=True)

# half smile (in-between)
t2 = cmds.duplicate(body, name="half_smile")[0]
verts2 = cmds.ls(f"{t2}.vtx[0:5]", flatten=True)
for v in verts2:
    p = cmds.pointPosition(v, w=True)
    cmds.move(p[0]+0.25, p[1], p[2], v, a=True, ws=True)

# live target
t3 = cmds.duplicate(body, name="live_target")[0]
verts3 = cmds.ls(f"{t3}.vtx[0:5]", flatten=True)
for v in verts3:
    p = cmds.pointPosition(v, w=True)
    cmds.move(p[0], p[1]+0.3, p[2], v, a=True, ws=True)

# BS node
bs = cmds.blendShape(t1, body, name="body_bs", frontOfChain=True)[0]
cmds.blendShape(bs, edit=True, inBetween=True, target=(body, 0, t2, 0.5))
cmds.blendShape(bs, edit=True, target=(body, 1, t3, 1.0))

print("=== aliasAttr ===")
aliases = cmds.aliasAttr(bs, query=True) or []
for i in range(0, len(aliases), 2):
    print(f"  {aliases[i]} -> {aliases[i+1]}")

print("=== weight attrs ===")
wattrs = cmds.listAttr(f"{bs}.weight", multi=True) or []
print(f"  {wattrs}")

print("=== target count ===")
print(f"  {cmds.blendShape(bs, query=True, weightCount=True)}")

# 测试提取
cmds.delete(t1, t2)  # 删除 static targets, 保留 live

from skills.sync_rig_incremental import _extract_blendshape_data
vis = cmds.listRelatives(body, shapes=True, fullPath=True, noIntermediate=True)[0]
data = _extract_blendshape_data(vis)

if data:
    print(f"\n=== extracted targets: {len(data['targets'])} ===")
    for t in data["targets"]:
        items_info = [(it["item_index"], f"max_delta={float(max(abs(it['delta'].flatten()))):.4f}") for it in t["items"]]
        print(f"  [{t['index']}] {t['name']}: items={items_info}")

maya.standalone.uninitialize()
