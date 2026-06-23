import maya.cmds as cmds
import json

def find_skin(xform):
    shapes = cmds.listRelatives(xform, shapes=True, noIntermediate=True, fullPath=True) or []
    out = []
    for shape in shapes:
        out += cmds.ls(cmds.listHistory(shape, pruneDagObjects=True) or [], type='skinCluster') or []
    return list(dict.fromkeys(out))

def info(name):
    matches = cmds.ls(name, type='transform', long=True) or []
    if not matches:
        return {'exists': False, 'name': name}
    x = matches[0]
    return {
        'exists': True,
        'transform': x,
        'vtx': cmds.polyEvaluate(x, vertex=True),
        'face': cmds.polyEvaluate(x, face=True),
        'skins': find_skin(x),
    }
result = {
    'scene': cmds.file(q=True, sceneName=True),
    'M_Head_base': info('M_Head_base'),
    'A': info('A'),
    'A_like': cmds.ls('A*', type='transform')[:30],
}
