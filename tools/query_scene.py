import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from dccs.maya.worker import MayaCommandPortWorker

def query_maya():
    worker = MayaCommandPortWorker(port=7002)
    try:
        worker.start()
    except Exception as e:
        print(f"无法连接到 Maya: {e}")
        return
        
    code = """
import maya.cmds as cmds
import traceback
result = {'status': 'SUCCESS'}
try:
    scene_info = {}
    
    # 检查 AA
    if cmds.objExists('AA'):
        shape = cmds.listRelatives('AA', shapes=True)
        if shape:
            vtx_count = cmds.polyEvaluate(shape[0], vertex=True)
            scene_info['AA'] = {'type': 'mesh', 'vertices': vtx_count}
        else:
            scene_info['AA'] = {'type': cmds.objectType('AA'), 'vertices': 0}
    else:
        scene_info['AA'] = 'Not Found'

    # 检查 HH
    if cmds.objExists('HH'):
        shape = cmds.listRelatives('HH', shapes=True)
        if shape:
            vtx_count = cmds.polyEvaluate(shape[0], vertex=True)
            scene_info['HH'] = {'type': 'mesh', 'vertices': vtx_count}
        else:
            scene_info['HH'] = {'type': cmds.objectType('HH'), 'vertices': 0}
    else:
        scene_info['HH'] = 'Not Found'

    # 获取场景中其他的顶层节点
    assemblies = cmds.ls(assemblies=True)
    scene_info['top_level_nodes'] = [node for node in assemblies if node not in ['persp', 'top', 'front', 'side']]

    result['scene_state'] = scene_info
except Exception as e:
    result['status'] = 'ERROR'
    result['error'] = traceback.format_exc()
"""

    payload = {
        'task_id': 'query_scene',
        'skill_id': 'exec_code',
        'parameters': {
            'code': code,
            'description': '查询场景状态'
        }
    }
    
    try:
        res = worker.run_skill(payload)
        print("====== 场景查询结果 ======")
        print(json.dumps(res, indent=2, ensure_ascii=False))
    finally:
        worker.shutdown()

if __name__ == "__main__":
    query_maya()
