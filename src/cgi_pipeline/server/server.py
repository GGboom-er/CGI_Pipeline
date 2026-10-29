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
from mcp.types import CallToolResult
from cgi_pipeline.paths import REPOSITORY_ROOT

load_dotenv(REPOSITORY_ROOT / '.env')

# ── 常量 ──
PROJECT_ROOT = REPOSITORY_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cgi_pipeline.catalog import list_capabilities
from cgi_pipeline.core.path_guard import describe_protected_roots
from cgi_pipeline.core.task_status import is_failure
from cgi_pipeline.server.mcp_audit import build_mcp_audit
from cgi_pipeline.server.public_surface import render_public_tool_routes


class PipelineMCP(FastMCP):
    """Preserve business receipts while marking terminal failures at transport exit.

    FastMCP 3.2 converts dict returns to content/structuredContent without an
    error flag. Keep this SDK-specific adaptation here, outside business owners
    and audit middleware; real Client tests guard its serialization contract.
    """

    async def _call_tool_mcp(self, key, arguments):
        result = await super()._call_tool_mcp(key, arguments)
        if isinstance(result, tuple):
            result = CallToolResult(content=result[0], structuredContent=result[1])
        if isinstance(result, CallToolResult):
            receipt = result.structuredContent
            # Explicit terminal failures only: PARTIAL, attention and running
            # states retain their own meaning; nested step errors are not the
            # overall operation status.
            status = receipt.get("status") if isinstance(receipt, dict) else None
            if is_failure(status):
                result = result.model_copy(update={"isError": True})
        return result


# ══════════════════════════════════════════════════
# Instructions 动态生成（英文路由决策树优先）
# ══════════════════════════════════════════════════

def _build_instructions() -> str:
    """Shared routing/safety contract; tool descriptions supply only their own delta."""
    # Claude Code reads only the first 4096 characters: the safety boundary
    # leads and the total stays within 3500. Moved details live in README
    # 「快速使用」 or the owning API's api_help.
    protected_roots = ', '.join(describe_protected_roots())

    return (
        "CGI Pipeline for Maya, Blender and Unreal Editor. Safety boundary: "
        f"protected roots {protected_roots} (backslash forms included); protected paths remain read-only in every mode. "
        "Never save over, rename, move or delete production assets there; stage copies into managed "
        "projects/ or runs/ roots. Use only this registered transport; never bypass it with client-side raw "
        "socket, subprocess or commandPort connections. Path guards are cooperative checks, not "
        "operating-system isolation for arbitrary Python.\n\n"
        f"{render_public_tool_routes()}\n\n"
        "Unknown API: read api_help and cache catalog_version; known contracts execute directly with "
        "expected_catalog_version, refreshing once on CATALOG_VERSION_STALE. list_workflows supplies input bindings.\n"
        "Calls wait by default, capped below the client tool timeout. PROGRESS keeps the task_id: call get_task_result, "
        "which waits server-side; do not poll in a loop, and do not resubmit after a client timeout. Results over 4 KiB "
        "return a summary with receipt_ref; use detail=true only when needed. PARTIAL is terminal, not full success. "
        "HTTP_DRAINING with submitted=false was not accepted; retry after the frontend resumes.\n"
        "Foreground calls operate the selected session's existing scene via the contract's port/session; workspace_id "
        "only links receipts. Authorized foreground work may use the actual project; each API keeps its own path limits. "
        "Independent sessions and output files may share the same workspace concurrently; do not wait for unrelated tasks. "
        "workspace.lock guards short metadata transactions only: never hold it across API calls or an entire batch, "
        "never delete it.\n"
        "Background initialization stages explicit source inputs. Inspect/preview-only Maya/Blender collections need "
        "a foreground session, source_path, workspace_id or scene_action=open; modes are intent, not permission. "
        "scene_action=continue keeps the resident unsaved scene; if its owner is lost, open a saved source (APIs are "
        "never replayed). Saving is a separate API (save_scene api_help).\n"
        "Resident workspaces bind isolated runtime lanes (default max 3); only resident capacity returns "
        "RUNTIME_CAPACITY_BUSY without dispatch. Retain runtime_id/generation. runtime_lifecycle=temporary workflows "
        "use their own workers and may execute concurrently in the same workspace; resident calls share one scene and "
        "remain serial per runtime. Duplicate operation delivery is fenced separately. pipeline.runtime.status/release "
        "need no free lane (release rules: api_help; memory, locks and cleanup: README 快速使用).\n"
        "project selects business rules; workspace_project selects storage, defaulting to project. Reuse the recorded "
        "workspace_project; new classified asset tasks use project/flow (for example ysj/proxyRig), unclassified tasks "
        "use default. business_task_id reuses projects/{workspace_project}/{business_task_id}; retain workspace_id. "
        "Sandbox creation rules and status codes: README 快速使用.\n"
        "The MCP surface is registry-controlled; use API/workflow metadata for operation details."
    )


# ══════════════════════════════════════════════════
# 初始化 MCP Server
# ══════════════════════════════════════════════════

@lifespan
async def _server_lifespan(_server):
    # MCP connections are lightweight frontends. Runtime startup is lazy at the
    # first execution request; closing any frontend must not stop shared work.
    from cgi_pipeline.core.service_manager import share_worker_runtime
    await __import__('asyncio').to_thread(share_worker_runtime)
    yield {}

mcp = PipelineMCP(
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
        # HTTP is optional; both transports share the same independently managed runtime.
        host = os.getenv('MCP_HOST', '127.0.0.1')
        port = int(os.getenv('MCP_PORT', '8000'))
        print(f'[cgi_pipeline_mcp] Streamable HTTP on http://{host}:{port}/mcp')
        print(f'[cgi_pipeline_mcp] {len(list_capabilities())} APIs registered')
        from cgi_pipeline.server.http_runtime import run_http
        run_http(mcp, host=host, port=port)
    else:
        # stdio 可能由 mcporter 以“一次调用、一个短进程”启动；后台 worker
        # 是跨 MCP 调用复用的独立服务，不能在本次 MCP 进程退出时被误杀。
        mcp.run(transport='stdio')
