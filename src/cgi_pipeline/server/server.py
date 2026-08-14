# cgi_pipeline/server/server.py
# ── CGI Pipeline MCP Server ──
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
from fastmcp.server.lifespan import lifespan

load_dotenv()

# ── 常量 ──
PROJECT_ROOT = Path(os.getenv('PROJECT_ROOT', '.'))

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cgi_pipeline.catalog import list_capabilities
from cgi_pipeline.core.manifest import get_manifest
from cgi_pipeline.server.mcp_audit import build_mcp_audit


# ══════════════════════════════════════════════════
# Instructions 动态生成（英文路由决策树优先）
# ══════════════════════════════════════════════════

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
    save_rule = 'mandatory' if chain_rules.get('save_scene_must_be_last') else 'recommended'
    abort_rule = 'mandatory' if chain_rules.get('abort_on_step_failure') else 'optional'

    return (
        "CGI/VFX Pipeline Automation Server — controls Maya, Blender, and Unreal Editor.\n"
        "\n"
        "## TOOL ROUTING — READ THIS FIRST\n"
        "\n"
        "CRITICAL: Do NOT open your own raw socket / subprocess / commandPort connection to Maya\n"
        "from the client side — route ALL Maya interactions through this server's tools.\n"
        "This server internally bridges to Maya via an RPyC-upgraded commandPort that is type-safe,\n"
        "thread-safe and session-persistent; rely on that sanctioned path instead of rolling your own\n"
        "(a hand-rolled commandPort only sends strings one-way and cannot return structured data reliably).\n"
        "\n"
        "### Operation contract\n"
        "- list_apis -> enumerate atomic APIs; api_help(api_id) -> read its manifest contract\n"
        "- execute_api(api_id, params, execution_mode, foreground_port, wait=True) -> run one atomic action and return its final receipt\n"
        "- get_task_result(task_id) -> recover a task after timeout, reconnect, or explicit wait=False\n"
        "- list_workflows -> enumerate registered workflows\n"
        "- pipeline_execute_workflow(..., wait=True) -> run multi-step or cross-DCC work and return final status, step checklist, and report path\n"
        "Foreground execution requires the session or port required by the API contract.\n"
        "Both atomic API and workflow calls wait for final results by default.\n"
        "\n"
        "## SANDBOX RULES\n"
        f"- {readonly_drives} = production server, strictly read-only\n"
        f"- UNC paths ({unc_prefixes}) = also read-only\n"
        "- Pass original paths (e.g. X:/Project/...); system auto-creates task sandbox\n"
        "- save_scene path must be within workspace; protected paths -> BLOCKED\n"
        "\n"
        "## STATUS CODES\n"
        f"{status_str}\n"
        "\n"
        "The active MCP surface is intentionally small: list_apis, api_help, execute_api, get_task_result, list_workflows, and pipeline_execute_workflow."
    )


# ══════════════════════════════════════════════════
# 初始化 MCP Server
# ══════════════════════════════════════════════════

@lifespan
async def _server_lifespan(_server):
    owns_worker = False
    if '--http' in sys.argv:
        from cgi_pipeline.core.service_manager import (
            claim_worker_owner,
            ensure_worker_healthy,
            get_worker_owner,
            is_worker_alive,
            shutdown_worker_owned_by,
        )

        was_running, _ = await __import__('asyncio').to_thread(is_worker_alive, 'cgi')
        ok, message = await __import__('asyncio').to_thread(ensure_worker_healthy, 'cgi')
        if not ok:
            raise RuntimeError(f'CGI Worker failed to start: {message}')
        # Adopt an unowned Worker after an upgrade or unclean legacy shutdown;
        # never replace an ownership record held by another HTTP MCP process.
        owns_worker = not was_running or not await __import__('asyncio').to_thread(get_worker_owner)
        if owns_worker:
            await __import__('asyncio').to_thread(claim_worker_owner, os.getpid())
    try:
        yield {}
    finally:
        if owns_worker:
            await __import__('asyncio').to_thread(shutdown_worker_owned_by, os.getpid())

mcp = FastMCP(
    name="cgi_pipeline_mcp",
    instructions=_build_instructions(),
    lifespan=_server_lifespan,
)
mcp.add_middleware(build_mcp_audit(PROJECT_ROOT))


# ══════════════════════════════════════════════════
# 注册所有 Tools
# ══════════════════════════════════════════════════

from cgi_pipeline.server.tools_readonly import register_readonly_tools
from cgi_pipeline.server.tools_operations import register_operation_tools

register_readonly_tools(mcp)
register_operation_tools(mcp)

# ── AI 工具面精简(2026-07-11):只暴露控制/派发/查询/调试按钮 ──
# 白名单单一真相源在 tools_operations.EXPOSED_TOOLS;startup 与 reload 共用同一 prune。
import asyncio as _asyncio
from cgi_pipeline.server.tools_operations import prune_tools_to_whitelist as _prune

try:
    _asyncio.run(_prune(mcp))
except RuntimeError:
    _loop = _asyncio.new_event_loop()
    _loop.run_until_complete(_prune(mcp))
    _loop.close()
except Exception as _e:
    import logging as _logging
    _logging.getLogger(__name__).warning("工具面剪枝失败(不影响功能): %s", _e)


# ══════════════════════════════════════════════════
# 启动入口
# ══════════════════════════════════════════════════

if __name__ == '__main__':
    if '--http' in sys.argv:
        # 生命周期只清理本 HTTP 进程创建的 Worker；不能全局关掉其他实例正在复用的队列。
        host = os.getenv('MCP_HOST', '127.0.0.1')
        port = int(os.getenv('MCP_PORT', '8000'))
        print(f'[cgi_pipeline_mcp] Streamable HTTP on http://{host}:{port}/mcp')
        print(f'[cgi_pipeline_mcp] {len(list_capabilities())} APIs registered')
        mcp.run(transport='streamable-http', host=host, port=port)
    else:
        # stdio 可能由 mcporter 以“一次调用、一个短进程”启动；后台 worker
        # 是跨 MCP 调用复用的独立服务，不能在本次 MCP 进程退出时被误杀。
        mcp.run(transport='stdio')
