# tools/check_pipeline_governance.py
# ── CGI_Pipeline 治理一致性检查 ──
# 锁定 MCP 注册 / API / workflow 不漂移的目标态。
# 用法: python tools/check_pipeline_governance.py   (exit 0=PASS, 1=FAIL)

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.api_registry import get_all_apis

TOOL_COUNT_LIMIT = 40

issues = []
warns = []

apis = get_all_apis()
api_ids = {item['api_id'] for item in apis if item.get('api_id')}


from mcp_server.tools_operations import EXPOSED_TOOLS

# 1. API manifest 必须有对应 api_help.md；API help 只作为渐进式阅读入口。
for spec in apis:
    api_id = spec['api_id']
    handler_module = str(spec.get('handler', '')).split(':', 1)[0]
    parts = handler_module.split('.')
    try:
        operation_dir = parts[parts.index('operations') + 1]
    except (ValueError, IndexError):
        operation_dir = parts[-1] if parts else ''
    help_path = ROOT / 'api' / 'operations' / operation_dir / 'api_help.md'
    if not help_path.exists():
        issues.append(f"API 缺少 api_help.md: {api_id}")

# 2. MCP 只保留 API 目录、API 执行和 workflow 入口。
allowed = {'list_apis', 'api_help', 'execute_api', 'list_workflows', 'pipeline_execute_workflow'}
unexpected = EXPOSED_TOOLS - allowed
missing = allowed - EXPOSED_TOOLS
if unexpected:
    issues.append(f"MCP 暴露了非 API/workflow 入口: {sorted(unexpected)}")
if missing:
    issues.append(f"MCP 缺少固定入口: {sorted(missing)}")

# 4. workflow 的 api_id 必须都解析得到
for wf in sorted((ROOT / 'workflows').glob('*.json')):
    try:
        data = json.loads(wf.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        issues.append(f"workflow {wf.name} JSON 非法: {e}")
        continue
    for step in data.get('steps', []):
        sid = step.get('api_id')
        if sid and sid not in api_ids:
            issues.append(f"workflow {wf.name} 引用不存在 api_id: {sid}")

# 5. 工具数 sane（<40 警告）
total = len(EXPOSED_TOOLS)
if total >= TOOL_COUNT_LIMIT:
    warns.append(f"MCP 工具数 {total} >= {TOOL_COUNT_LIMIT}，考虑精简")

print(f"apis={len(apis)} exposed_tools={len(EXPOSED_TOOLS)} total={total}")
print("exposed:", sorted(EXPOSED_TOOLS))
for w in warns:
    print("WARN:", w)
for i in issues:
    print("FAIL:", i)
print("RESULT:", "FAIL" if issues else "PASS")
sys.exit(1 if issues else 0)
