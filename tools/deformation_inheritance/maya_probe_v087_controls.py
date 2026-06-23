import maya.cmds as cmds
import json
names = sorted(cmds.ls('*_A_ctrl', type='transform') or [])
interesting = [n for n in names if any(t in n.lower() for t in ['head','jaw','cheek','lid','eye','nose','lip','mouth','chin'])]
result = {'scene': cmds.file(q=True, sceneName=True), 'count': len(names), 'interesting_count': len(interesting), 'interesting': interesting[:200]}
