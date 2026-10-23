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
from cgi_pipeline.core.manifest import get_manifest
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
    m = get_manifest()
    principles = m.get('principles', {})
    status_codes = m.get('status_codes', {})

    readonly_drives = ', '.join(d.upper() + ':/' for d in principles.get('readonly_drives', []))
    unc_prefixes = ', '.join(principles.get('readonly_unc_prefixes', []))
    status_str = ' | '.join(f'{k}={v}' for k, v in status_codes.items())

    return (
        "CGI Pipeline: Maya, Blender and Unreal Editor. Use this registered transport; "
        "do not create client-side raw socket, subprocess or commandPort connections.\n"
        f"{render_public_tool_routes()}\n"
        "Unknown API: read api_help, cache catalog_version. Known contract: execute directly with "
        "expected_catalog_version; refresh once on CATALOG_VERSION_STALE. Workflow list supplies input bindings.\n"
        "Calls wait by default. For long workflows, use wait=False, keep the returned task_id, "
        "and recover via get_task_result; do not resubmit after a client timeout. PARTIAL is terminal, not full success.\n"
        "During a frontend update, HTTP_DRAINING with submitted=false means this call was not accepted; "
        "reconnect after the frontend resumes before retrying it. Accepted tasks retain their task_id and "
        "must be recovered without resubmission.\n"
        "Foreground calls operate the selected session's existing scene using the contract's port/session; workspace_id "
        "links receipts without staging. Authorized foreground work may use the actual project; each API retains "
        "its own path restrictions. Background scene initialization stages explicit source inputs.\n"
        "For a non-empty call collection containing only Maya/Blender inspect or preview operations, select a "
        "foreground session, source_path or existing workspace_id, or explicitly use scene_action=open. "
        "Mixed apply/query collections may initialize a scene. Modes describe intent, not permission or absence "
        "of side effects; inspect effects and access.\n"
        "workspace_id with scene_action=continue preserves a resident unsaved scene. If its owner is lost, "
        "explicitly open a saved source; prior APIs are never replayed. Saving is an independent API choice. "
        "Maya and Blender save_scene restrict current and target paths to managed projects/runs roots, not only the "
        "current run_dir; both use the same OS directory lock for next-version saving.\n"
        "Resident API/editing workspaces bind isolated runtime lanes (default maximum 3, with memory admission). "
        "Automated workflow variants declare runtime_lifecycle=temporary: independent operation-owned workers, "
        "no resident-slot cap, automatic memory waiting and own-runtime cleanup after verified success. "
        "Failures retain diagnostics and their own scene. Same-workspace execution stays serial. "
        "Only resident capacity returns RUNTIME_CAPACITY_BUSY without dispatch. Retain runtime_id/generation. "
        "pipeline.runtime.status/release are standalone control APIs and need no free lane. "
        "Release refuses pending/active work and dirty/unknown scenes unless discard is explicit.\n"
        "project selects business rules; workspace_project selects storage, defaulting to project when omitted. "
        "Use workspace_project=default for task sandboxes. Pass business_task_id to reuse "
        "projects/{workspace_project}/{business_task_id}; retain workspace_id. "
        "Existing UE editor and read-only Pipeline calls need no sandbox unless workspace_id, source_path "
        "or scene_action=open is explicit.\n"
        f"Protected roots: {readonly_drives}; {unc_prefixes}; protected paths remain read-only in every mode. "
        "Path guards are cooperative checks, not operating-system isolation for arbitrary Python.\n"
        f"{status_str}\n"
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
