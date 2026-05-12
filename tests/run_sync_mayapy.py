import sys
import os
import json

import maya.standalone
maya.standalone.initialize(name='python')

import maya.cmds as cmds

pipeline_path = r"y:\GGbommer\scripts\CGI_Pipeline"
if pipeline_path not in sys.path:
    sys.path.insert(0, pipeline_path)

from skills.sync_rig_asset import execute as sync_execute

rig_file = r"s:\project\ysj\work\assets\chr\xtbyao\rig\rigMaster\ysj_chr_xtbyao_rig_rigMaster_v002.ma"
cmds.file(rig_file, open=True, force=True)

# 搞乱测试
orig_node = "|Group|Geometry|cache|xtbyao_cloth_Grp|xtbyao_shoes_Grp|xtbyao_L_shoe_Grp|xtbyao_L_shoe1|xtbyao_L_shoe1ShapeOrig"
if not cmds.objExists(orig_node):
    cands = cmds.ls("*L_shoe1ShapeOrig", long=True)
    if cands: orig_node = cands[0]

result = {"orig_node": orig_node}

if cmds.objExists(orig_node):
    import maya.api.OpenMaya as om2
    sel = om2.MSelectionList()
    sel.add(orig_node)
    dag = sel.getDagPath(0)
    fn = om2.MFnMesh(dag)
    
    # 破坏
    pts = fn.getPoints(om2.MSpace.kObject)
    pt0_init = [pts[0].x, pts[0].y, pts[0].z]
    pts[0].y += 0.004  # 搞乱（小幅度，老 P0-A 用来触发 AUTO_SAFE 分支；当前映射为 Step 1/2 ORIG_INJECT）
    fn.setPoints(pts, om2.MSpace.kObject)
    pt0_destroyed = [pts[0].x, pts[0].y, pts[0].z]
    
    # Sync
    payload = {
        "source_path": rig_file,
        "parameters": {
            "tex_json": r"y:\runs\assets\chr\xtbyao\tex\texMaster\ysj_chr_xtbyao_tex_texMaster_v002.abc",
            "dry_run": False
        }
    }
    print("Executing Sync...")
    sync_result = sync_execute(payload)
    
    # 验证恢复
    pts_after = fn.getPoints(om2.MSpace.kObject)
    pt0_restored = [pts_after[0].x, pts_after[0].y, pts_after[0].z]
    
    result.update({
        "pt0_init": pt0_init,
        "pt0_destroyed": pt0_destroyed,
        "pt0_restored": pt0_restored,
        "sync_receipt": sync_result["items"]
    })
else:
    result["error"] = "Target orig node not found."

print("=== MAYAPY RESULT ===")
for r in result.get("sync_receipt", []):
    print(r)
print("=====================")
maya.standalone.uninitialize()

