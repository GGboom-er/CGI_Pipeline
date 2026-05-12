# verify_mayapy.py
# ── CGI Pipeline v2.0 — Phase 0：mayapy 隔离启动验证 ──
# 说明：验证 mayapy 在白名单环境下能正常启动，不继承父进程环境变量

import subprocess
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

MAYAPY = os.getenv('MAYAPY_PATH')
MAYA_BIN_DIR = os.getenv('MAYA_BIN_DIR', r'C:\Program Files\Autodesk\Maya2025\bin')

if not MAYAPY or not Path(MAYAPY).exists():
    print(f'[FAIL] MAYAPY_PATH 未配置或文件不存在: {MAYAPY}')
    print('       请在 .env 中设置 MAYAPY_PATH=<mayapy.exe 完整路径>')
    sys.exit(1)

# ── 白名单环境变量（禁止继承 os.environ）──
clean_env = {
    'SYSTEMROOT':       'C:\\Windows',
    'SYSTEMDRIVE':      'C:',
    'PATH':             f'{MAYA_BIN_DIR};C:\\Windows\\System32',
    'TEMP':             'C:\\Temp',
    'TMP':              'C:\\Temp',
    'MAYA_NO_HOME':     '1',        # 禁止加载用户 Maya 配置
    'MAYA_DISABLE_CIP': '1',        # 禁止 CIP 遥测
}

# ── 验证脚本（在 mayapy 内执行）──
test_script = """
import sys
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.api.OpenMaya as om

print('MAYA_VERSION=' + cmds.about(version=True))
print('PYTHON_VERSION=' + sys.version.split()[0])
print('API_VERSION=' + str(om.MGlobal.apiVersion()))

# 基本功能验证
cube = cmds.polyCube(name='verify_cube')[0]
assert cmds.objExists(cube), 'polyCube 创建失败'
cmds.delete(cube)
print('BASIC_OPS=PASS')

maya.standalone.uninitialize()
"""

print('=== mayapy 隔离启动验证 ===')
print(f'MAYAPY: {MAYAPY}')
print(f'环境变量数: {len(clean_env)} (白名单模式)')
print()

try:
    # 将测试脚本写入临时文件以避免 Windows -c 命令行的 unicode 编码问题
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.py', delete=False, mode='w', encoding='utf-8') as f:
        f.write(test_script)
        tmp_file = f.name

    result = subprocess.run(
        [MAYAPY, tmp_file],
        env=clean_env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=60,
    )
    os.remove(tmp_file)

    # 解析输出
    output = result.stdout + result.stderr
    print('--- Maya 输出 ---')
    print(output)
    print('-----------------')

    checks = {}
    for line in output.split('\n'):
        if '=' in line and line.split('=')[0] in ('MAYA_VERSION', 'PYTHON_VERSION', 'API_VERSION', 'BASIC_OPS'):
            key, val = line.split('=', 1)
            checks[key.strip()] = val.strip()

    # 验证结果
    all_pass = True
    for key in ('MAYA_VERSION', 'PYTHON_VERSION', 'API_VERSION', 'BASIC_OPS'):
        if key in checks:
            print(f'  [PASS] {key} = {checks[key]}')
        else:
            print(f'  [FAIL] {key} 未检测到')
            all_pass = False

    if result.returncode != 0:
        print(f'\n  [FAIL] mayapy 退出码: {result.returncode}')
        all_pass = False

    if all_pass:
        print('\nPASS: mayapy 隔离启动验证通过')
    else:
        print('\nFAIL: 请检查 Maya 安装和环境配置')
        sys.exit(1)

except subprocess.TimeoutExpired:
    print('[FAIL] mayapy 启动超时 (>60s)')
    sys.exit(1)
except FileNotFoundError:
    print(f'[FAIL] 找不到 mayapy: {MAYAPY}')
    sys.exit(1)
