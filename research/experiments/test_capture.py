"""测试 Maya 视口截图 — 使用持久化连接，验证无 EOFError"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dccs.maya.worker import MayaCommandPortWorker

worker = MayaCommandPortWorker(port=7029)
print("Starting worker...")
worker.start()

# 第一次调用：截取 Maya 窗口
print("\n=== Test 1: Maya Window Capture ===")
res1 = worker.run_skill({
    'task_id': 'test-window-capture',
    'skill_id': 'maya_capture_viewport',
    'parameters': {'mode': 'window', 'width': 1920, 'height': 1080}
})
print(json.dumps(json.loads(res1['detail']) if isinstance(res1.get('detail'), str) else res1, indent=2, ensure_ascii=False))

# 第二次调用（复用同一连接，不应有 EOFError）：截取纯 3D 视口
print("\n=== Test 2: Viewport Capture (reusing connection) ===")
res2 = worker.run_skill({
    'task_id': 'test-viewport-capture',
    'skill_id': 'maya_capture_viewport',
    'parameters': {'mode': 'viewport', 'width': 1280, 'height': 720}
})
print(json.dumps(json.loads(res2['detail']) if isinstance(res2.get('detail'), str) else res2, indent=2, ensure_ascii=False))

# 优雅退出
worker.shutdown()
print("\nWorker shutdown complete. Check Maya Script Editor - should be ZERO EOFError!")
