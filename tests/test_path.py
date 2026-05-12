import sys
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline')
from skills.maya_sync_rig_incremental.maya_sync_rig_incremental import _get_target_name_and_parent, _ensure_hierarchy
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds

cmds.file(r'Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260503_235714_mihouwang_cruise_test\ysj_chr_mihouwang_rig_rigMaster_v002.ma', open=True, force=True)

# Simulate what maya_sync_rig_incremental does to |cache
for node in cmds.ls("cache", long=True, type="transform") or []:
    if "|cache" in node or node == "cache":
        cache_node = node
        break

if cache_node:
    from skills.maya_sync_rig_incremental.maya_sync_rig_incremental import _add_prefix_recursive
    _add_prefix_recursive(cache_node, "RIG_")

dag_tex = 'ABC|Group|cache|mihouwang_hiddenMesh_Grp|mihouwang_body1_hairbasemesh|mihouwang_body1_hairbasemeshShape'
target_name, group_parts = _get_target_name_and_parent(dag_tex)
parent_path = _ensure_hierarchy(group_parts)

print("TARGET_NAME:", target_name)
print("GROUP_PARTS:", group_parts)
print("PARENT_PATH:", parent_path)
