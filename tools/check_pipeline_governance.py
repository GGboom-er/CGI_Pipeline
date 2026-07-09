# tools/check_pipeline_governance.py
# ── CGI_Pipeline 治理一致性检查 ──
# 锁定 MCP 注册 / skill / workflow 不漂移的目标态（P1 MCP 归一的防回弹闸）。
# 用法: python tools/check_pipeline_governance.py   (exit 0=PASS, 1=FAIL)

import sys
import re
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.skill_registry import get_all_skills

TOOL_COUNT_LIMIT = 40

issues = []
warns = []

skills = get_all_skills()
skill_ids = {s['skill_id'] for s in skills if s.get('skill_id')}
exposed = [s['skill_id'] for s in skills if s.get('mcp_expose')]


def _manual_tool_names(rel):
    """提取 @mcp.tool(name="...") 手动注册的工具名。"""
    src = (ROOT / rel).read_text(encoding='utf-8')
    return set(re.findall(r'@mcp\.tool\(.*?name="([^"]+)"', src, re.S))


manual = _manual_tool_names('mcp_server/tools_operations.py') | _manual_tool_names('mcp_server/tools_readonly.py')

# 1. mcp_expose 的 skill 必须有 SKILL.md 目录
for sid in exposed:
    if not (ROOT / 'skills' / sid / 'SKILL.md').exists():
        issues.append(f"mcp_expose skill 无 SKILL.md: {sid}")

# 2. 不得双轨注册：mcp_expose 的 skill_id 不能同时是手动工具名
for sid in exposed:
    if sid in manual:
        issues.append(f"双轨注册: '{sid}' 既 mcp_expose 又有同名手动 Tool")

# 3. 黑名单不得复活（P1.3 已删，改白名单 mcp_expose）
ops_src = (ROOT / 'mcp_server' / 'tools_operations.py').read_text(encoding='utf-8')
if 'EXCLUDED_DYNAMIC_SKILLS' in ops_src:
    issues.append("EXCLUDED_DYNAMIC_SKILLS 黑名单复活（应为白名单 mcp_expose）")

# 4. workflow 的 skill_id 必须都解析得到
for wf in sorted((ROOT / 'workflows').glob('*.json')):
    try:
        data = json.loads(wf.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        issues.append(f"workflow {wf.name} JSON 非法: {e}")
        continue
    for step in data.get('steps', []):
        sid = step.get('skill_id')
        if sid and sid not in skill_ids:
            issues.append(f"workflow {wf.name} 引用不存在 skill_id: {sid}")

# 5. 工具数 sane（<40 警告）
total = len(exposed) + len(manual)
if total >= TOOL_COUNT_LIMIT:
    warns.append(f"MCP 工具数 {total} >= {TOOL_COUNT_LIMIT}，考虑精简")

print(f"skills={len(skills)} mcp_expose={len(exposed)} manual_tools={len(manual)} total={total}")
print("manual:", sorted(manual))
for w in warns:
    print("WARN:", w)
for i in issues:
    print("FAIL:", i)
print("RESULT:", "FAIL" if issues else "PASS")
sys.exit(1 if issues else 0)
