"""验证生产技能通过 IPC 在 Maya 中执行"""
import time, sys, os
from dotenv import load_dotenv
load_dotenv()

mayapy = os.getenv('MAYAPY_PATH')
if not mayapy or not os.path.exists(mayapy):
    print(f'[SKIP] MAYAPY_PATH 未配置: {mayapy}')
    sys.exit(0)

from dccs.maya.worker import MayaWorker

print('=== 生产技能联调测试 ===')
w = MayaWorker()
print(f'Worker ID: {w.worker_id}')

print('启动 Maya...')
try:
    w.start()
except RuntimeError as e:
    print(f'[FAIL] Maya 启动失败: {e}')
    sys.exit(1)
print('Maya 启动成功')

# 测试 1: ping（只读操作，最安全）
print('\n--- 测试 ping ---')
payload = {
    'task_id': 'test-ping-001',
    'skill_id': 'ping',
    'project': 'test',
    'asset_name': 'default_scene',
}
t0 = time.time()
try:
    result = w.run_skill(payload)
    elapsed = time.time() - t0
    print(f'结果: {result["status"]}')
    print(f'详情: {result["detail"][:300]}')
    print(f'耗时: {elapsed:.2f}s')
    assert result['status'] == 'SUCCESS', f'FAIL: {result}'
    print('[PASS] ping')
except Exception as e:
    print(f'[FAIL] {e}')

# 测试 2: freeze_transforms（在默认空场景上执行）
print('\n--- 测试 freeze_transforms ---')
payload2 = {
    'task_id': 'test-freeze-001',
    'skill_id': 'freeze_transforms',
    'project': 'test',
    'asset_name': 'default_scene',
}
t0 = time.time()
try:
    result2 = w.run_skill(payload2)
    elapsed = time.time() - t0
    print(f'结果: {result2["status"]}')
    print(f'详情: {result2["detail"][:300]}')
    print(f'耗时: {elapsed:.2f}s')
    assert result2['status'] == 'SUCCESS', f'FAIL: {result2}'
    print('[PASS] freeze_transforms')
except Exception as e:
    print(f'[FAIL] {e}')

# 清理
if w.process and w.process.poll() is None:
    w.process.terminate()
    w.process.wait(timeout=10)

print('\n=== 联调测试完成 ===')
