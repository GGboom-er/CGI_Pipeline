"""验证 MCP Server 注册表加载"""
import sys, os
sys.path.insert(0, r'y:\GGbommer\scripts\CGI_Pipeline')
os.chdir(r'y:\GGbommer\scripts\CGI_Pipeline')
os.environ['PROJECT_ROOT'] = r'y:\GGbommer\scripts\CGI_Pipeline'

import json
from pathlib import Path

# 1. 验证注册表文件
reg = json.loads(Path('skills/registry.json').read_text(encoding='utf-8'))
print(f'[PASS] 注册表加载: {len(reg)} 个技能')
for s in reg:
    print(f'  - {s["skill_id"]}: {s["name"]} ({s["dcc"]})')

# 2. 验证 MCP Server 加载
from mcp_server.server import _SKILLS, _SKILL_MAP
print(f'\n[PASS] MCP 技能映射: {list(_SKILL_MAP.keys())}')

# 3. 验证所有技能模块文件存在
failed = False
for s in reg:
    sid = s['skill_id']
    skill_path = Path(f'skills/{sid}.py')
    status = 'PASS' if skill_path.exists() else 'FAIL'
    if status == 'FAIL':
        failed = True
    print(f'[{status}] 技能文件: {skill_path} (dcc={s["dcc"]})')

# 4. 验证 DCC 分布
maya_skills = [s for s in reg if s['dcc'] == 'maya']
blender_skills = [s for s in reg if s['dcc'] == 'blender']
print(f'\n[INFO] Maya 技能: {len(maya_skills)} 个')
print(f'[INFO] Blender 技能: {len(blender_skills)} 个')

if failed:
    print('\n[FAIL] 存在缺失的技能文件')
    sys.exit(1)
else:
    print('\n全部验证通过')
