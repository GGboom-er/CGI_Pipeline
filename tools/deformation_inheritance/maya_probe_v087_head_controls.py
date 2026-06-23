import maya.cmds as cmds, json
patterns=['*Head*ctrl','*Head*_ctrl','*Neck*ctrl','*head*ctrl','*neck*ctrl']
result={p: sorted(cmds.ls(p, type='transform') or []) for p in patterns}
result['scene']=cmds.file(q=True, sceneName=True)
