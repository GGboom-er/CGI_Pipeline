# tools/check_pipeline_governance.py
# ── CGI_Pipeline 治理一致性检查 ──
# 锁定 MCP 注册 / API / workflow 不漂移的目标态。
# 用法: python tools/check_pipeline_governance.py   (exit 0=PASS, 1=FAIL)

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'
sys.path.insert(0, str(SRC))
from cgi_pipeline.catalog import list_capabilities

TOOL_COUNT_LIMIT = 40

issues = []
warns = []

apis = list_capabilities()
api_ids = {item['api_id'] for item in apis if item.get('api_id')}


from cgi_pipeline.server.tools_operations import EXPOSED_TOOLS

# 1. API manifest 是唯一参数契约；其 handler 必须与 manifest 同目录可解析。
for spec in apis:
    api_id = spec['api_id']
    handler_module, _, handler_name = str(spec.get('handler', '')).partition(':')
    handler_parts = handler_module.split('.')
    handler_path = SRC.joinpath(*handler_parts).with_suffix('.py')
    manifest_path = ROOT / spec.get('manifest_path', '')
    if not manifest_path.is_file():
        issues.append(f"capability manifest 不可解析: {api_id} -> {manifest_path}")
        continue
    if not handler_name or not handler_path.exists():
        issues.append(f"API handler 不可解析: {api_id} -> {spec.get('handler', '')}")
    elif handler_path.parent != manifest_path.parent:
        issues.append(f"API handler 不在 manifest 目录: {api_id}")

# 2. 派生 catalog 必须与 manifest 和 package registry 一致。
import subprocess
catalog_check = subprocess.run(
    [sys.executable, str(ROOT / 'tools' / 'build_catalogs.py'), '--check'],
    cwd=ROOT,
    capture_output=True,
    text=True,
)
if catalog_check.returncode:
    issues.append('派生 catalog 已漂移；运行 tools/build_catalogs.py 重新生成')

# 3. MCP 只保留 API 目录、API 执行和 workflow 入口。
allowed = {
    'list_apis', 'api_help', 'execute_api', 'get_task_result',
    'list_workflows', 'pipeline_execute_workflow',
}
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
