import sys
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline')
import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
cmds.file(r'Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260503_235714_mihouwang_cruise_test\ysj_chr_mihouwang_rig_rigMaster_v002.ma', open=True, force=True)

import skills.maya_sync_rig_incremental.maya_sync_rig_incremental as sync_mod
from core.abc_reader import read_abc_as_info
from skills.pipeline_compare_asset import pipeline_compare_asset

abc_path = r'Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260503_235714_mihouwang_cruise_test\tex.abc'
tex_info = read_abc_as_info(abc_path)

rig_info = {"meshes": sync_mod._collect_rig_meshes()}

comp_report = pipeline_compare_asset.compare(tex_info, rig_info)
groups = comp_report.get('pairing_groups', [])
target_only = comp_report.get('target_only_dags', [])

match = [g for g in groups if any('body1' in d for d in g.get('abc_dags', []))]
print('=== PAIRING GROUPS ===')
print('TOTAL groups:', len(groups), ' target_only:', len(target_only))
for g in match:
    print('  ', g)

