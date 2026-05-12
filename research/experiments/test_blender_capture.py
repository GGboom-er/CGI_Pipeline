import json
from dccs.blender.worker import BlenderWorker

worker = BlenderWorker()
print("Starting Blender worker...")
worker.start()

payload = {
    'task_id': 'test-blender-capture',
    'skill_id': 'blender_capture_viewport',
    'parameters': {
        'width': 1280,
        'height': 720
    }
}

try:
    print("Running skill...")
    res = worker.run_skill(payload)
    print("Capture result:")
    print(json.dumps(res, indent=2))
finally:
    worker.shutdown()
