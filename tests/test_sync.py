import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds
import sys

cmds.file(r'Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260503_235714_mihouwang_cruise_test\ysj_chr_mihouwang_rig_rigMaster_v002.ma', open=True, force=True)

sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline')
from skills.maya_sync_rig_incremental.maya_sync_rig_incremental import execute

payload = {
    'parameters': {
        'abc_path': r'Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260503_235714_mihouwang_cruise_test\tex.abc',
        'tex_json': r'Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260503_235714_mihouwang_cruise_test\tex_info.json'
    },
    'source_path': 'dummy'
}

execute(payload)

print('CHILDREN OF cache:')
cache_grp = cmds.ls('cache', long=True)[0]
for child in cmds.listRelatives(cache_grp, allDescendents=True, fullPath=True) or []:
    if 'mihouwang_body' in child or 'hairsui' in child:
        print('  ', child)
