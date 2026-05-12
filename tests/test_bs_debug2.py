# test_bs_debug2.py — 验证删除 static target 后 plug 数据是否存在
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
from maya.api import OpenMaya as om2
import sys

cmds.file(new=True, force=True)
body = cmds.polyCube(name="Body")[0]
t1 = cmds.duplicate(body, name="smile")[0]
verts = cmds.ls(f"{t1}.vtx[0:3]", flatten=True)
for v in verts:
    p = cmds.pointPosition(v, w=True)
    cmds.move(p[0]+1, p[1], p[2], v, a=True, ws=True)

bs = cmds.blendShape(t1, body, name="test_bs")[0]
print("=== BEFORE delete ===")
cmds.setAttr(f"{bs}.smile", 1.0)
print(f"  weight = {cmds.getAttr(f'{bs}.smile')}")

# 检查 plug 数据
sel = om2.MSelectionList(); sel.add(bs)
fn = om2.MFnDependencyNode(sel.getDependNode(0))
it = fn.findPlug("inputTarget", False)
gi = it.getExistingArrayAttributeIndices()
print(f"  geom indices: {gi}")
itg = it.elementByLogicalIndex(gi[0]).child(0)
ti = itg.getExistingArrayAttributeIndices()
print(f"  target indices: {ti}")

for tidx in ti:
    tp = itg.elementByLogicalIndex(tidx)
    iti = tp.child(0)
    items = iti.getExistingArrayAttributeIndices()
    print(f"  target[{tidx}] item indices: {items}")
    for ii in items:
        ip = iti.elementByLogicalIndex(ii)
        for ci in range(ip.numChildren()):
            ch = ip.child(ci)
            aname = om2.MFnAttribute(ch.attribute()).name
            if aname == "inputPointsTarget":
                try:
                    d = ch.asMDataHandle().data()
                    if not d.isNull():
                        pts = om2.MFnPointArrayData(d).array()
                        print(f"    item[{ii}] ipt: {len(pts)} points")
                    else:
                        print(f"    item[{ii}] ipt: NULL")
                except Exception as e:
                    print(f"    item[{ii}] ipt: ERROR {e}")

print("\n=== AFTER delete target mesh ===")
cmds.delete(t1)

for tidx in ti:
    tp = itg.elementByLogicalIndex(tidx)
    iti = tp.child(0)
    items = iti.getExistingArrayAttributeIndices()
    print(f"  target[{tidx}] item indices: {items}")
    for ii in items:
        ip = iti.elementByLogicalIndex(ii)
        for ci in range(ip.numChildren()):
            ch = ip.child(ci)
            aname = om2.MFnAttribute(ch.attribute()).name
            if aname == "inputPointsTarget":
                try:
                    d = ch.asMDataHandle().data()
                    if not d.isNull():
                        pts = om2.MFnPointArrayData(d).array()
                        print(f"    item[{ii}] ipt: {len(pts)} points")
                    else:
                        print(f"    item[{ii}] ipt: NULL")
                except Exception as e:
                    print(f"    item[{ii}] ipt: ERROR {e}")
            elif aname == "inputGeomTarget":
                conns = cmds.listConnections(ch.name(), s=True, d=False) or []
                print(f"    item[{ii}] igt connections: {conns}")

maya.standalone.uninitialize()
