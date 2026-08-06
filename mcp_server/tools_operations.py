# mcp_server/tools_operations.py
# ── 操作类 MCP Tools ──
# 从 server.py 拆分：所有修改场景数据或触发执行的 Tool

import asyncio
import sys

from mcp_server.models import (
    ExecCodeInput,
    UEExecCodeInput,
    UEActionInput,
    ExecuteChainInput,
    ExecuteApiInput,
    StartWorkerInput,
    CopyFilesInput,
    CompareAssetInput,
    SyncRigAssetInput,
    ExecuteWorkflowInput,
)
from mcp_server.internals import (
    _submit_to_celery,
    _submit_api_to_celery,
    _submit_chain,
    _submit_workflow,
    _ensure_worker,
    PROJECT_ROOT,
)

async def _execute_ue_action(foreground_port, action, payload, timeout):
    """WebSocket 直连 UE_MCP_Bridge，执行插件的原生 method。

    action 取插件原生 method 名，payload 用插件侧原生参数名，结果原样返回。
    """
    from dccs.ue import foreground_client

    return await asyncio.to_thread(
        foreground_client.execute_action,
        foreground_port,
        action,
        payload,
        timeout,
    )


# ── AI 工具面白名单(单一真相源)──
# 研究(Harness 130→11、IBM"只暴露业务级"、Anthropic 有效工具):工具越少 AI 选得越准。
# API 能力统一经 execute_api 或 workflow 入口触达，不再动态注册第二套具名入口。
# 收/放某按钮:改本集合 + reload 即生效,可回滚。startup(server.py)与 reload 共用本 prune。
EXPOSED_TOOLS = {
    "pipeline_execute_workflow", "list_workflows",
    "list_apis", "api_help", "execute_api",
}


async def prune_tools_to_whitelist(mcp):
    """只保留 API 目录、API 执行和 workflow 入口。"""
    for t in await mcp.list_tools():
        name = getattr(t, "name", None)
        if name and name not in EXPOSED_TOOLS:
            try:
                # FastMCP 3.x：local_provider 是工具注册表的正式删除入口。
                mcp.local_provider.remove_tool(name)
            except Exception:
                pass


# 端口扫描抽到共享叶子模块 mcp_server.ports（消除 tools_readonly/tools_operations/foreground_client 三处重复）
from mcp_server.ports import discover_maya_ports


def _require_explicit_foreground_port(params, tool_name, dcc='maya'):
    """foreground mode requires an explicit port for the matching DCC."""
    if getattr(params, 'execution_mode', 'background') != 'foreground':
        return None
    if getattr(params, 'foreground_port', None) is not None:
        return None
    if dcc == 'blender':
        from dccs.blender.foreground_client import discover_blender_sessions
        sessions = discover_blender_sessions()
        return {
            'status': 'NEEDS_ATTENTION',
            'tool': tool_name,
            'error': 'foreground 模式必须显式传 foreground_port，避免误连其他 Blender 会话。',
            'active_ports': [session['port'] for session in sessions],
            'active_sessions': sessions,
            'recovery_hint': '先用 blender_list_foreground_sessions 确认会话，再显式传 foreground_port。',
        }
    return {
        'status': 'NEEDS_ATTENTION',
        'tool': tool_name,
        'error': 'foreground 模式必须显式传 foreground_port，避免多 Maya commandPort 会话时误连。',
        'active_ports': discover_maya_ports(),
        'recovery_hint': '先用 maya_list_foreground_sessions 确认端口，再显式传 foreground_port。Maya 端建议开启：cmds.commandPort(name=":7009", sourceType="python", echoOutput=True)',
    }


def register_operation_tools(mcp):
    """将所有操作类 Tools 注册到 MCP Server 实例"""

    @mcp.tool(
        name="execute_api",
        annotations={
            "title": "执行一个 CGI 原子 API",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def execute_api_tool(params: ExecuteApiInput) -> dict:
        """Execute one catalog API through the existing DCC Worker path.

        API is the deterministic layer: use api_help first for inputs and
        operation modes. Workflows remain the composition layer.
        """
        context = {
            'execution_mode': params.execution_mode,
            'foreground_port': params.foreground_port,
            'project': params.project,
            'asset_name': params.asset_name,
            'source_path': params.source_path,
            'sync': params.sync,
        }
        return _submit_api_to_celery(params.api_id, {
            'project': params.project,
            'asset_name': params.asset_name,
            'source_path': params.source_path,
            'params': params.params,
            'context': context,
        })

    # ── 高级 Tools ──

    @mcp.tool(
        name="maya_start_worker",
        annotations={
            "title": "静默启动 DCC 后台 Worker",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def maya_start_worker(params: StartWorkerInput) -> dict:
        """在后台启动 Celery Worker 进程（幂等，已运行则跳过）。

        这是所有操作类 Tool 的前置条件。如果提交任务后持续 NOT_FOUND，说明 Worker 未启动。
        使用 pidfile 防重复：Worker 已在运行时直接返回 SUCCESS。
        现在提交任务时会自动拉起 Worker，通常不需要手动调用此接口。
        """
        if params.dcc not in ("cgi", "maya", "blender", "ue", "workflow", "pipeline"):
            return {"status": "ERROR", "message": f"暂不支持启动 {params.dcc} worker"}

        dcc = 'cgi'
        pidfile = PROJECT_ROOT / 'runtime' / f'worker_{dcc}.pid'

        from core.service_manager import ensure_worker_healthy, get_worker_health
        ok, message = ensure_worker_healthy(dcc)
        if ok:
            return {
                "status": "SUCCESS",
                "message": message,
                "health": get_worker_health(dcc),
            }

        _ensure_worker(dcc)

        # 读取新 pid
        import time as _t
        _t.sleep(1)
        if pidfile.exists():
            try:
                pid = int(pidfile.read_text().strip())
                return {
                    "status": "SUCCESS",
                    "pid": pid,
                    "python": sys.executable,
                    "pidfile": str(pidfile),
                    "message": f"成功启动 {dcc} Worker (PID={pid})",
                }
            except (ValueError, OSError):
                pass

        return {"status": "ERROR", "message": message or f"{dcc} Worker 启动失败"}


    @mcp.tool(
        name="pipeline_restart_worker",
        annotations={
            "title": "重启后台 Worker",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def pipeline_restart_worker(params: StartWorkerInput) -> dict:
        """重启指定后台 Worker。用于 PID 存活但 Celery 心跳丢失、队列无响应等场景。"""
        if params.dcc not in ("cgi", "maya", "blender", "ue", "workflow", "pipeline"):
            return {"status": "ERROR", "message": f"暂不支持重启 {params.dcc} worker"}
        dcc = 'cgi'
        from core.service_manager import restart_worker, get_worker_health
        ok = restart_worker(dcc)
        return {
            "status": "SUCCESS" if ok else "ERROR",
            "dcc": dcc,
            "health": get_worker_health(dcc),
            "message": f"{dcc} Worker 已重启" if ok else f"{dcc} Worker 重启失败",
        }


    @mcp.tool(
        name="maya_exec_code",
        annotations={
            "title": "在 Maya 中执行 Python 代码",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def maya_exec_code(params: ExecCodeInput) -> dict:
        """Execute Python code in Maya. Use this INSTEAD OF raw socket or commandPort.

        This tool provides an RPyC-upgraded connection that is type-safe, thread-safe,
        and runs code on Maya's main thread (safe for all cmds/OpenMaya calls).

        For user's currently open Maya (interactive):
          1. Call maya_list_foreground_sessions first to get the port
          2. Set execution_mode='foreground', foreground_port=<discovered_port>
          3. sync=True (default): instant response like Script Editor, no polling needed

        For batch processing (headless):
          Set execution_mode='background' (default), results via maya_query_task

        Code must assign results to `result` variable (dict).
        Example: result = {'status': 'SUCCESS', 'meshes': cmds.ls(type='mesh')}

        在 Maya 中执行 Python 代码。禁止裸 socket 连接，必须通过本工具。
        """
        guard = _require_explicit_foreground_port(params, 'maya_exec_code')
        if guard:
            return guard

        # foreground 交互场景默认开 sync：跟 Script Editor 一样随手敲、亚秒级拿结果
        effective_sync = bool(params.sync) if params.execution_mode == 'foreground' else False
        return _submit_to_celery('exec_code', {
            'project': params.project,
            'asset_name': params.asset_name,
            'source_path': params.source_path,
            'parameters': {
                'code': params.code,
                'description': params.description,
                'execution_mode': params.execution_mode,
                'foreground_port': params.foreground_port,
                'sync': effective_sync,
            },
        })


    @mcp.tool(
        name="ue_exec_code",
        annotations={
            "title": "在 Unreal Editor 中执行 Python 代码",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def ue_exec_code(params: UEExecCodeInput) -> dict:
        """Execute Python in the selected open Unreal Editor.

        Call ue_list_foreground_sessions first and pass its foreground_port.
        The UE localhost bridge is internal transport; this tool is the sole
        AI-facing UE execution entry. Code may print and may assign a JSON-
        serializable value to ``result``. The response includes result, stdout,
        stderr, traceback, session identity, timeout, and connection failures.
        """
        from dccs.ue.foreground_client import execute_python

        return await asyncio.to_thread(
            execute_python,
            params.foreground_port,
            params.code,
            params.description,
            params.timeout_seconds,
        )


    @mcp.tool(
        name="ue_exec_action",
        annotations={
            "title": "调用 Unreal Editor 结构化动作",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def ue_exec_action(params: UEActionInput) -> dict:
        """Call an existing structured Unreal plugin action through CGI Pipeline.

        Use this for editor operations that standard UE Python does not expose,
        such as Blueprint graph editing. Clients still connect only to the
        cgi_pipeline MCP.
        """
        return await _execute_ue_action(
            params.foreground_port,
            params.action,
            params.payload,
            params.timeout_seconds,
        )


    @mcp.tool(
        name="maya_execute_chain",
        annotations={
            "title": "链式批处理（多 API 顺序执行）",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def maya_execute_chain(params: ExecuteChainInput) -> dict:
        """一次性提交多步 API 链，在同一个 DCC 会话中顺序执行。支持 Maya 和 Blender。

        异步执行，返回 task_id。链引擎自动打开 source_path，步骤中不需要再传 source_path。
        DCC 类型由第一个 API 的 manifest 自动推断（Maya API → Maya 进程，Blender API → Blender 进程）。
        链内所有 API 必须属于同一个 DCC，混合链会被拒绝。

        关键规则：
        - save_scene 必须放在链的最后一步（Maya 链）
        - 任何步骤失败 → 链中断，返回 CHAIN_ABORTED
        - 步骤返回 AUDIT_FAILED/BLOCKED/ERROR → 链中止并生成报告；后台流程不等待人工继续

        Maya 链典型用法：maya.rig.clean_skinweights → maya.asset.save_scene
        Blender 链典型用法：blender.asset.blender_export_abc → blender.asset.blender_build_asset_info
        """
        guard = _require_explicit_foreground_port(params, 'maya_execute_chain')
        if guard:
            return guard

        chain_data = [{'api_id': s.api_id, 'parameters': s.parameters} for s in params.api_chain]
        return _submit_chain({
            'source_path': params.source_path,
            'project': params.project,
            'asset_name': params.asset_name,
            'api_chain': chain_data,
            'execution_mode': params.execution_mode,
            'foreground_port': params.foreground_port,
        })


    # ── 平台级 Tools ──

    @mcp.tool(
        name="pipeline_copy_files",
        annotations={
            "title": "文件拷贝",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def pipeline_copy_files(params: CopyFilesInput) -> dict:
        """通用文件拷贝：source → destination，文件或目录均可。

        异步执行，返回 task_id。纯文件系统操作，不启动任何 DCC 进程。
        source 是文件则拷贝单个文件，是目录则递归拷贝整个目录。
        AI 可并行调用多次实现批量拷贝。
        """
        return _submit_to_celery('copy_files', {
            'project': 'pipeline',
            'asset_name': 'copy',
            'source_path': '',
            'parameters': {
                'source': params.source,
                'destination': params.destination,
                'overwrite': params.overwrite,
            },
        })


    @mcp.tool(
        name="pipeline_compare_asset",
        annotations={
            "title": "资产对比（JSON/ABC）",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def pipeline_compare_asset(params: CompareAssetInput) -> dict:
        """资产对比（仅支持 _info.json / .abc 输入）。

        异步执行，返回 task_id。任务框架会创建沙盒和 .info 目录；
        compare_result.json 写入沙盒 .info，Markdown 内容进入统一任务报告。
        DCC 源文件请先通过对应采集/导出 API 转换为 _info.json 或 .abc。
        """
        return _submit_to_celery('pipeline_compare_asset', {
            'project': params.project,
            'asset_name': params.asset_name,
            'source_path': '',
            'parameters': {
                'input_source': params.input_source,
                'input_target': params.input_target,
                'label_source': params.label_source,
                'label_target': params.label_target,
            }
        })


    @mcp.tool(
        name="maya_sync_rig_incremental",
        annotations={
            "title": "同步资产到绑定文件",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def maya_sync_rig_incremental(params: SyncRigAssetInput) -> dict:
        """在 target rig 场景里按 compare_result 增量重建/更新 mesh。

        异步执行（Maya 链），返回 task_id。
        `source_path` 是 Celery 框架层键名——这里承载 **target 侧 rig 场景**。
        `compare_result` 必须来自前置对比 API。
        `source_abc` 或 `source_info` 至少填一个；同时提供时优先 ABC。
        操作包裹在 undo chunk 中。
        """
        return _submit_to_celery('maya_sync_rig_incremental', {
            'project': params.project,
            'asset_name': params.asset_name,
            'source_path': params.source_path,
            'parameters': {
                'compare_result': params.compare_result,
                'source_abc': params.source_abc,
                'source_info': params.source_info,
                'cache_group': params.cache_group,
                'dry_run': params.dry_run,
            },
        })


    # ── 工作流执行 ──

    @mcp.tool(
        name="pipeline_execute_workflow",
        annotations={
            "title": "执行工作流（跨 DCC 编排）",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def execute_workflow_tool(params: ExecuteWorkflowInput) -> dict:
        """执行指定工作流（跑注册工作流的唯一入口）。支持跨 DCC 编排（Blender + Maya + pipeline 混合）。

        wait=True(默认): 一次调用阻塞到工作流终态，直接返回最终 status + 每步 ✓/✗ 清单
        (step_checklist) + report_path，不用再手动轮询。wait=False: 提交即返回 task_id，
        自行用 maya_query_task 轮询。工作流引擎自动按 DCC 类型分段执行、自动拉起所需 worker、
        段间传递输出（{{outputs.step_id.field}}）——调用方无需手动开 worker 或写轮询。

        典型用法：
        - blender_tex_export: Blender 导出 ABC + 采集 info
        - tex_to_rig_verify: 资产名/显式路径 → Blender source → Maya rig 校验（跨 DCC）
        - tex_to_rig_verify_and_sync: 资产名/显式路径 → 对比 → 拼装 → 材质 → 保存
        - full_cleanup_and_save: 权重清理 → 全清理 → Shape 修复 → 法线统一 → 保存
        - abc_import_with_materials: ABC 导入 + UDIM 材质分配
        """
        submit = _submit_workflow({
            'workflow_id': params.workflow_id,
            'source_path': params.source_path,
            'project': params.project,
            'asset_name': params.asset_name,
            'extra_params': params.extra_params or {},
        })
        if not params.wait or submit.get('status') != 'SUBMITTED':
            return submit  # 提交失败或 wait=False：保持原语义返回

        # ── wait=True：阻塞轮询到终态，附每步 ✓/✗ 清单 ──
        import asyncio
        from core.task_status import is_terminal
        from mcp_server.internals import _read_audit, collect_workflow_steps, render_step_checklist

        task_id = submit['task_id']
        deadline = asyncio.get_event_loop().time() + params.wait_timeout_sec
        state = {}
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(params.poll_interval_sec)
            state = await asyncio.to_thread(_read_audit, task_id)
            if is_terminal(state.get('status', '')):
                break
        else:
            # 超时：工作流仍在后台跑，诚实返回，不谎报完成
            return {
                'task_id': task_id,
                'workflow_id': params.workflow_id,
                'status': 'PROGRESS',
                'message': f'已等待 {params.wait_timeout_sec}s 未达终态，工作流仍在后台运行。',
                'recovery_hint': f'用 maya_query_task(task_id="{task_id}") 续查，或加大 wait_timeout_sec。',
            }

        steps = await asyncio.to_thread(collect_workflow_steps, task_id)
        final_status = state.get('status', 'UNKNOWN')
        result = {
            'task_id': task_id,
            'workflow_id': params.workflow_id,
            'status': final_status,
            'steps': steps,
            'step_checklist': render_step_checklist(steps),
        }
        for k in ('report_path', 'audit_path'):
            if state.get(k):
                result[k] = state[k]
        if final_status not in ('WORKFLOW_SUCCESS', 'SUCCESS', 'CHAIN_SUCCESS'):
            result['recovery_hint'] = f'工作流未成功({final_status})。看 step_checklist 里 ✗ 的步骤 + report_path 定位。'
        return result


    # ── 热重载 ──

    @mcp.tool(
        name="reload_server",
        annotations={
            "title": "热重载 MCP Server",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def reload_server() -> dict:
        """热重载 MCP Server 业务模块，使 core/、api/ 和 DCC 适配器修改立即生效。

        清除已缓存的 Python 模块，移除并重新注册所有 Tool handler（替换旧闭包），
        使 tools_readonly/tools_operations 改动对客户端立即生效。不会中断 MCP 连接。
        """
        import importlib
        reloaded = []
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith(('core.', 'api.operations.', 'mcp_server.', 'dccs.')):
                try:
                    importlib.reload(sys.modules[mod_name])
                    reloaded.append(mod_name)
                except Exception:
                    pass

        from mcp_server.internals import reload_internals
        reload_internals()

        # ── 核心：重新注册 Tool handler，替换启动时绑定的旧闭包 ──
        # FastMCP 的 @mcp.tool 装饰器把 handler 函数对象存进内部 registry，
        # 模块 reload 只刷新 module globals，不会替换 registry 里的 function object。
        # 所以必须先 remove 后 re-register。
        re_registered = 0
        try:
            tools_before = await mcp.list_tools()
            for t in tools_before:
                try:
                    mcp.remove_tool(t.name)
                except Exception:
                    pass
            # 重新 import 并调用 register 函数（reload 后它们是新定义）
            import mcp_server.tools_readonly as _tr
            import mcp_server.tools_operations as _top
            _tr.register_readonly_tools(mcp)
            _top.register_operation_tools(mcp)
            # 重注册后同样按白名单剪枝,否则 reload 会让被收的按钮全回来
            await _top.prune_tools_to_whitelist(mcp)
            tools_after = await mcp.list_tools()
            re_registered = len(tools_after)
        except Exception as e:
            return {
                "status": "PARTIAL",
                "reloaded_modules": reloaded,
                "error": f"模块 reload 成功但 Tool re-register 失败: {e!r}",
                "message": "需要彻底重启 MCP server 进程",
            }

        from mcp_server.internals import _APIS
        return {
            "status": "RELOADED",
            "reloaded_modules": reloaded,
            "apis_count": len(_APIS),
            "tools_reregistered": re_registered,
            "message": f"已热重载 {len(reloaded)} 个模块，重注册 {re_registered} 个 Tool，{len(_APIS)} 个 API",
        }
