# core/workflow_engine.py
# 工作流定义加载器 — 只负责读取 JSON，不执行
#
# 执行逻辑统一走 core/tasks.py 的 execute_skill_chain。
# 本模块仅保留工作流 JSON 的发现和加载能力。

import json
from pathlib import Path

_WORKFLOWS_DIR = Path(__file__).parent.parent / 'workflows'


def list_workflows() -> list[dict]:
    """列出所有已注册的工作流及其步骤概要"""
    results = []
    if not _WORKFLOWS_DIR.exists():
        return results
    for f in sorted(_WORKFLOWS_DIR.glob('*.json')):
        try:
            wf = json.loads(f.read_text(encoding='utf-8'))
            results.append({
                'workflow_id': wf['workflow_id'],
                'name': wf.get('name', ''),
                'status': wf.get('status', 'active'),
                'description': wf.get('description', ''),
                'steps': [s['skill_id'] for s in wf.get('steps', [])],
            })
        except Exception:
            pass
    return results


def load_workflow(workflow_id: str) -> dict:
    """根据 workflow_id 加载完整工作流定义"""
    for f in _WORKFLOWS_DIR.glob('*.json'):
        wf = json.loads(f.read_text(encoding='utf-8'))
        if wf.get('workflow_id') == workflow_id:
            return wf
    raise FileNotFoundError(f'工作流不存在: {workflow_id}')
