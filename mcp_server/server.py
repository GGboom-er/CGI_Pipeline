# mcp_server/server.py
# ── CGI Pipeline MCP Server v3.2 ──
# 基于 Anthropic mcp-builder 官方规范
#
# 架构拆分：
#   models.py          — Pydantic Input Models
#   internals.py       — Celery 提交、审计读取、Worker 管理
#   tools_readonly.py  — 只读 Tools（查询类）
#   tools_operations.py — 操作 Tools（修改场景/执行任务）
#   server.py          — FastMCP 初始化 + 注册入口（本文件）
#
# 命名规范：cgi_pipeline_mcp（snake_case + _mcp 后缀）
# Tool 前缀：maya_（预留 blender_/houdini_ 扩展）
# 输入校验：Pydantic BaseModel + ConfigDict
# 注解标准：readOnlyHint / destructiveHint / idempotentHint / openWorldHint

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

# ── 常量 ──
PROJECT_ROOT = Path(os.getenv('PROJECT_ROOT', '.'))

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.skill_registry import get_all_skills
from core.manifest import get_manifest


# ══════════════════════════════════════════════════
# Instructions 动态生成（英文路由决策树优先）
# ══════════════════════════════════════════════════

_SKILLS = get_all_skills()


def _build_instructions() -> str:
    """从 manifest + registry 动态生成 MCP instructions（英文路由决策树优先）

    设计意图：
    - 英文路由决策树置顶，让所有 AI 客户端第一时间知道"用什么工具"
    - 明确禁止裸 socket，引导 AI 走 MCP 工具链
    - 运行时规则保留但精简，降低上下文 token 消耗
    """
    m = get_manifest()
    principles = m.get('principles', {})
    status_codes = m.get('status_codes', {})
    safety_rules = m.get('exec_code_safety', [])
    chain_rules = m.get('chain_rules', {})

    readonly_drives = ', '.join(d.upper() + ':/' for d in principles.get('readonly_drives', []))
    unc_prefixes = ', '.join(principles.get('readonly_unc_prefixes', []))
    status_str = ' | '.join(f'{k}={v}' for k, v in status_codes.items())
    safety_str = '\n'.join(f'  - {r}' for r in safety_rules)
    skill_ids = ', '.join(s['skill_id'] for s in _SKILLS)

    save_rule = 'mandatory' if chain_rules.get('save_scene_must_be_last') else 'recommended'
    abort_rule = 'mandatory' if chain_rules.get('abort_on_step_failure') else 'optional'

    return (
        "CGI/VFX Pipeline Automation Server — controls Maya/Blender for asset processing.\n"
        "\n"
        "## TOOL ROUTING — READ THIS FIRST\n"
        "\n"
        "CRITICAL: Do NOT open your own raw socket / subprocess / commandPort connection to Maya\n"
        "from the client side — route ALL Maya interactions through this server's tools.\n"
        "This server internally bridges to Maya via an RPyC-upgraded commandPort that is type-safe,\n"
        "thread-safe and session-persistent; rely on that sanctioned path instead of rolling your own\n"
        "(a hand-rolled commandPort only sends strings one-way and cannot return structured data reliably).\n"
        "\n"
        "### Interactive Maya (user has Maya open)\n"
        "1. FIRST call maya_list_foreground_sessions -> get active port list\n"
        "2. THEN call maya_exec_code with execution_mode='foreground' and foreground_port=<port>\n"
        "   - sync=True (default): instant response, no polling needed\n"
        "   - NEVER guess or hardcode port numbers\n"
        "3. For named operations, also pass execution_mode='foreground' and foreground_port\n"
        "\n"
        "### Batch Processing (no user Maya open)\n"
        "- Single operation: use named tools (maya_clean_skinweights, maya_build_asset_info, etc.)\n"
        "- Multi-step same DCC: maya_execute_chain (shared session, one submission)\n"
        "- Cross-DCC pipeline: pipeline_execute_workflow\n"
        "\n"
        "### Asset Discovery\n"
        "maya_resolve_asset -> returns source_path -> pass directly to operation tools\n"
        "System auto-creates sandbox copies; always pass original paths as-is.\n"
        "\n"
        "### Task Monitoring\n"
        "All operations are async: return task_id -> poll with maya_query_task(task_id)\n"
        "Poll interval: 3-5s. NOT_FOUND = task not yet picked up, retry.\n"
        "Exception: maya_exec_code(sync=True) returns result directly, no polling needed.\n"
        "\n"
        "## TOOL PRIORITY\n"
        "named_tool > maya_exec_code > execute_skill (fallback for unlisted skills)\n"
        "Low-frequency tools can be invoked via execute_skill(skill_id=...).\n"
        "\n"
        "## SANDBOX RULES\n"
        f"- {readonly_drives} = production server, strictly read-only\n"
        f"- UNC paths ({unc_prefixes}) = also read-only\n"
        "- Pass original paths (e.g. X:/Project/...); system auto-creates task sandbox\n"
        "- save_scene path must be within workspace; protected paths -> BLOCKED\n"
        "\n"
        "## CHAIN RULES (maya_execute_chain)\n"
        "- Engine auto-opens source_path; do NOT re-open in step parameters\n"
        "- Multi-step same-DCC tasks MUST use chain (shared Maya/Blender process)\n"
        f"- save_scene MUST be last step ({save_rule})\n"
        f"- Any step failure -> CHAIN_ABORTED ({abort_rule})\n"
        "- AUDIT_FAILED/BLOCKED/ERROR -> chain stops, generates report\n"
        "\n"
        "## exec_code RULES\n"
        "- Assign results to `result` variable (dict): result = {'status': 'SUCCESS', 'data': ...}\n"
        "- Safety constraints:\n"
        f"{safety_str}\n"
        "\n"
        "## STATUS CODES\n"
        f"{status_str}\n"
        "\n"
        f"Registered skills: {skill_ids}. Call maya_list_skills for full parameter schemas."
    )


# ══════════════════════════════════════════════════
# 初始化 MCP Server
# ══════════════════════════════════════════════════

mcp = FastMCP(
    name="cgi_pipeline_mcp",
    instructions=_build_instructions(),
)


# ══════════════════════════════════════════════════
# 注册所有 Tools
# ══════════════════════════════════════════════════

from mcp_server.tools_readonly import register_readonly_tools
from mcp_server.tools_operations import register_operation_tools

register_readonly_tools(mcp)
register_operation_tools(mcp)


# ══════════════════════════════════════════════════
# 注册动态资源 (Resources)
# ══════════════════════════════════════════════════

def _query_maya_selection(port: int) -> str:
    # 服务端受控的 commandPort 桥（不是 instructions 里禁止的"客户端自建裸连接"）：
    # 读前台选择集，统一经 maya://{port}/selection resource 对外，客户端不直连。
    import json, socket
    code = "__import__('json').dumps({'selection': __import__('maya.cmds').cmds.ls(sl=True, long=True)})"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(('127.0.0.1', port))
        s.sendall(code.encode('utf-8'))
        raw = s.recv(4096).decode('utf-8')
        s.close()
        
        for line in reversed(raw.split('\n')):
            line = line.strip().strip('\x00')
            if line.startswith('{'):
                return line
        return json.dumps({'error': f'无法解析 Maya 返回数据: {repr(raw)[:100]}'})
    except Exception as e:
        return json.dumps({'error': str(e)})


@mcp.resource("maya://{port}/selection")
async def read_maya_selection(port: str) -> str:
    """动态读取指定 Maya 端口的前台选择集"""
    return _query_maya_selection(int(port))


# ══════════════════════════════════════════════════
# 启动入口
# ══════════════════════════════════════════════════

if __name__ == '__main__':
    # 长驻进程：装退出钩子，MCP 退出时 reap 它拉起的 worker，堵最大孤儿源
    # （MCP 懒起 worker 却从不 reap → MCP 一死 worker 成孤儿）。install_exit_hooks 幂等。
    try:
        from core.service_manager import install_exit_hooks
        install_exit_hooks()
    except Exception as _e:
        print(f'[cgi_pipeline_mcp] install_exit_hooks 失败(非致命): {_e}')
    if '--http' in sys.argv:
        host = os.getenv('MCP_HOST', '0.0.0.0')
        port = int(os.getenv('MCP_PORT', '8000'))
        print(f'[cgi_pipeline_mcp] Streamable HTTP on http://{host}:{port}/mcp')
        print(f'[cgi_pipeline_mcp] {len(_SKILLS)} skills registered')
        mcp.run(transport='streamable-http', host=host, port=port)
    else:
        mcp.run(transport='stdio')
