import json
from dccs.maya.worker import MayaCommandPortWorker

worker = MayaCommandPortWorker(port=7029)
worker.start()
print("RPyC bootstrapped successfully on port", worker.rpyc_port)

payload = {
    'task_id': 'test-cube',
    'skill_id': 'exec_code',
    'parameters': {
        'code': '''import maya.cmds as cmds
cube = cmds.polyCube(name="RPyC_Cube_Magic")[0]
cmds.move(0, 5, 0, cube)
result = {"status": "SUCCESS", "message": f"Created {cube} using RPyC!"}
'''
    }
}

res = worker.run_skill(payload)
print("Exec result:", json.dumps(res, indent=2))
