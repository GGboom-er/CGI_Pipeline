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


def check_workflows(workflow_dir, api_ids):
    """Check public workflows through the execution loader and finite compiler."""
    from cgi_pipeline.core.workflow_compiler import iter_workflow_variants
    from cgi_pipeline.core.workflow_engine import load_workflow
    from cgi_pipeline.core.workflow_templates import step_failure_policy
    issues, count, seen = [], 0, set()
    for path in sorted(Path(workflow_dir).glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            workflow_id = data.get('workflow_id')
            if not isinstance(workflow_id, str) or not workflow_id or workflow_id in seen:
                raise ValueError('workflow_id must be unique and nonempty')
            seen.add(workflow_id)
            raw = load_workflow(workflow_id)
            for controls, plan in iter_workflow_variants(raw):
                count += 1
                for step in plan['steps']:
                    repair = step_failure_policy(step).get('repair_step', {})
                    for candidate in (step, repair):
                        if candidate and candidate.get('api_id') not in api_ids:
                            issues.append(f'workflow {path.name} controls={controls} '
                                          f'step={step["step_id"]} 引用不存在 api_id: '
                                          f'{candidate.get("api_id")}')
        except Exception as exc:
            # Only explicit reject policies are skipped, by the compiler owner.
            issues.append(f'workflow {path.name} 不可编译: {exc}')
    return issues, count


def main():
    """Keep command execution out of imports so checks have testable owners."""
    import subprocess
    from cgi_pipeline.server.public_surface import (
        BASELINE_TOOLS, EXPOSED_TOOLS, PUBLIC_TOOL_ROLES, PUBLIC_TOOL_ROUTES,
    )
    issues, warns = [], []
    apis = list_capabilities()
    api_ids = {item['api_id'] for item in apis if item.get('api_id')}
    # Shared handlers stay under their executor's registered capability root.
    for spec in apis:
        api_id = spec['api_id']
        executor = str(spec.get('executor', ''))
        handler_module, _, handler_name = str(spec.get('handler', '')).partition(':')
        handler_path = SRC.joinpath(*handler_module.split('.')).with_suffix('.py')
        manifest_path = ROOT / spec.get('manifest_path', '')
        executor_root = SRC / 'cgi_pipeline' / 'capabilities' / executor
        if not manifest_path.is_file():
            issues.append(f'capability manifest 不可解析: {api_id} -> {manifest_path}')
        elif not handler_name or not handler_path.exists():
            issues.append(f'API handler 不可解析: {api_id} -> {spec.get("handler", "")}')
        elif (not manifest_path.resolve().is_relative_to(executor_root.resolve()) or
              not handler_path.resolve().is_relative_to(executor_root.resolve())):
            issues.append(f'API manifest/handler 越出 executor capability 根: {api_id}')
    catalog_check = subprocess.run(
        [sys.executable, '-B', str(ROOT / 'tools/build_catalogs.py'), '--check'],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    if catalog_check.returncode:
        issues.append('派生 catalog 已漂移；运行 tools/build_catalogs.py 重新生成')
    missing = BASELINE_TOOLS - EXPOSED_TOOLS
    if missing:
        issues.append(f'MCP 缺少兼容基线入口: {sorted(missing)}')
    if set(PUBLIC_TOOL_ROUTES) != set(EXPOSED_TOOLS):
        issues.append('MCP public route registry 与暴露白名单不一致')
    if set(PUBLIC_TOOL_ROLES) != set(EXPOSED_TOOLS):
        issues.append('MCP public role registry 与暴露白名单不一致')
    host_specific = {name for name in EXPOSED_TOOLS
                     if name.startswith(('maya_', 'blender_', 'ue_', 'houdini_'))}
    if host_specific:
        issues.append(f'MCP 门面不得按 DCC 扩张: {sorted(host_specific)}')
    workflow_issues, variants = check_workflows(ROOT / 'workflows', api_ids)
    issues.extend(workflow_issues)
    total = len(EXPOSED_TOOLS)
    if total >= TOOL_COUNT_LIMIT:
        warns.append(f'MCP 工具数 {total} >= {TOOL_COUNT_LIMIT}，考虑精简')
    print(f'apis={len(apis)} exposed_tools={total} total={total} workflow_variants={variants}')
    print('exposed:', sorted(EXPOSED_TOOLS))
    for warning in warns:
        print('WARN:', warning)
    for issue in issues:
        print('FAIL:', issue)
    print('RESULT:', 'FAIL' if issues else 'PASS')
    return 1 if issues else 0


if __name__ == '__main__':
    sys.exit(main())
