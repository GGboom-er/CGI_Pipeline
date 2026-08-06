# mcp_server/tools_readonly.py
# ── 只读 MCP Tools ──
# 从 server.py 拆分：所有不修改场景数据的查询类 Tool

from mcp_server.models import (
    TaskQueryInput,
    ResolveAssetInput,
    ResolveShotInput,
)
from mcp_server.internals import _read_audit


def register_readonly_tools(mcp):
    """将所有只读 Tools 注册到 MCP Server 实例"""

    @mcp.tool(
        name="list_apis",
        annotations={
            "title": "List available APIs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def list_apis_tool() -> dict:
        """Return the one-line API catalog without starting a DCC."""
        from api.registry import list_apis
        rows = list_apis()
        return {
            'total': len(rows),
            'apis': [
                {
                    'api_id': row['api_id'],
                    'version': row['version'],
                    'dcc': row['dcc'],
                    'domain': row['domain'],
                    'action': row['action'],
                    'tier': row['tier'],
                    'execution_modes': row['execution_modes'],
                    'operation_modes': row['operation_modes'],
                    'help': row.get('help', {}),
                    'summary': row.get('help', {}).get('summary', ''),
                    'scenario': row.get('help', {}).get('scenario', ''),
                }
                for row in rows
            ],
            'usage': '先 api_help，再 execute_api(api_id, params=...)。',
        }


    @mcp.tool(
        name="api_help",
        annotations={
            "title": "Show one CGI API contract",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def api_help_tool(api_id: str) -> dict:
        """Return manifest-backed inputs, modes, side effects and recovery."""
        from api.registry import api_help
        try:
            return api_help(api_id)
        except KeyError as exc:
            return {
                'status': 'ERROR',
                'error_code': 'API_NOT_FOUND',
                'error': str(exc),
                'recovery_hint': '调用 list_apis 查看已登记 API。',
            }


    @mcp.tool(
        name="maya_query_task",
        annotations={
            "title": "Poll async task status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def maya_query_task(params: TaskQueryInput) -> dict:
        """Poll the execution status of an async task.

        All operation tools return task_id after submission.
        Use this to poll until terminal state is reached.
        State flow: SUBMITTED -> STARTED/PROGRESS -> SUCCESS / ERROR / TIMEOUT / BLOCKED / AUDIT_FAILED / CHAIN_ABORTED
        SUBMITTED = accepted by the submission path but not yet confirmed started by a worker.
        NOT_FOUND is reserved for unknown/legacy task IDs; it is not a normal queue state.
        Exception: maya_exec_code(sync=True) returns result directly, no polling needed.

        轮询异步任务状态。所有操作类 Tool 提交后返回 task_id；排队任务至少有 SUBMITTED 记录。
        """
        return _read_audit(params.task_id)


    @mcp.tool(
        name="maya_resolve_asset",
        annotations={
            "title": "Resolve asset paths and versions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def maya_resolve_asset(params: ResolveAssetInput) -> dict:
        """Resolve an asset's latest version path and version list. Read-only.

        MUST be called before any asset operation to get source_path.
        Pass the returned path directly to operation tools.
        Without stage, auto-selects by pipeline priority (model: tex>uv>mod, rig: lib>rig>lyrig).
        Pass task to locate sub-task directories (e.g. bs, paintTex).

        操作前必须先调用此工具获取 source_path，再传给操作类 Tool。
        """
        try:
            from core.config_loader import load_project_config
            from core.asset_resolver import AssetResolver
            cfg = load_project_config(params.project)
            resolver = AssetResolver(cfg)
            result = resolver.resolve_for_mcp(
                category=params.category,
                asset_name=params.asset_name,
                stage=params.stage,
                task=params.task,
                pipeline=params.pipeline,
            )
            result['project'] = params.project
            return result
        except Exception as e:
            import traceback
            return {
                'status': 'ERROR',
                'error': str(e),
                'traceback': traceback.format_exc(),
                'recovery_hint': '项目或资产名称可能不正确，请用文件系统工具确认目录结构。',
            }


    @mcp.tool(
        name="maya_resolve_shot",
        annotations={
            "title": "Resolve shot paths and versions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def maya_resolve_shot(params: ResolveShotInput) -> dict:
        """Resolve a shot's latest version path and version list. Read-only.

        For locating shot workflow files (ly/ani/cfx/efx/lgt/mat stages).
        Without stage, returns all available stages for the shot.

        查询镜头的最新版本路径和版本列表。只读，不启动 DCC。
        """
        try:
            from core.config_loader import load_project_config
            from core.asset_resolver import AssetResolver
            cfg = load_project_config(params.project)
            resolver = AssetResolver(cfg)
            task = params.task or None

            if params.stage:
                latest = resolver.resolve_shot_latest(
                    params.sequence, params.shot, params.stage, task
                )
                versions = resolver.list_shot_versions(
                    params.sequence, params.shot, params.stage, task
                )
                shot_stages = resolver.get_shot_stages()
                stage_info = shot_stages.get(params.stage, {})
                return {
                    'project': params.project,
                    'sequence': params.sequence,
                    'shot': params.shot,
                    'stage': params.stage,
                    'task': task or stage_info.get('primary_task', ''),
                    'latest_publish': str(latest) if latest else None,
                    'latest_exists': latest.exists() if latest else False,
                    'versions': versions,
                    'total_versions': len(versions),
                }
            else:
                shot_stages = resolver.get_shot_stages()
                results = {}
                for stg, stg_cfg in shot_stages.items():
                    latest = resolver.resolve_shot_latest(
                        params.sequence, params.shot, stg
                    )
                    if latest:
                        results[stg] = {
                            'task': stg_cfg.get('primary_task', ''),
                            'latest_publish': str(latest),
                            'exists': latest.exists(),
                        }
                return {
                    'project': params.project,
                    'sequence': params.sequence,
                    'shot': params.shot,
                    'available_stages': results,
                    'all_stages': list(shot_stages.keys()),
                }
        except Exception as e:
            import traceback
            return {
                'status': 'ERROR',
                'error': str(e),
                'traceback': traceback.format_exc(),
            }


    @mcp.tool(
        name="list_workflows",
        annotations={
            "title": "List available workflows",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def list_workflows_tool() -> dict:
        """List all registered workflows and their descriptions.

        Read-only query, no DCC process started.
        Returns workflow ID, name, description, and API steps.

        列出所有已注册的工作流及其描述和步骤。
        """
        from core.workflow_engine import list_workflows
        workflows = list_workflows()
        return {
            'count': len(workflows),
            'workflows': workflows,
        }


    @mcp.tool(
        name="pipeline_service_status",
        annotations={
            "title": "Check service and worker heartbeat",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def pipeline_service_status() -> dict:
        """Check Redis, Maya/Blender/Workflow Worker PID and Celery queue heartbeat.

        查询 Redis、各 Worker 的 PID 与 Celery 队列心跳。
        """
        from core.service_manager import get_service_status
        return {
            'status': 'SUCCESS',
            'services': get_service_status(include_heartbeat=True),
        }


    @mcp.tool(
        name="pipeline_explain_architecture",
        annotations={
            "title": "Pipeline architecture whitepaper",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def pipeline_explain_architecture() -> dict:
        """Retrieve CGI Pipeline v2 architecture design whitepaper.

        Call this when you need to understand the pipeline's async execution,
        sandbox paths, receipt structure, or exception handling.

        获取 CGI Pipeline v2 的架构设计白皮书与底层逻辑。
        """
        import os
        from pathlib import Path
        project_root = Path(os.getenv('PROJECT_ROOT', '.'))
        docs = []
        for rel in (
            'docs/README.md',
            'docs/pipeline_runtime_contract_v1.md',
            'docs/compare_and_assembly_pipeline_plan.md',
        ):
            doc_path = project_root / rel
            if doc_path.exists():
                docs.append(f'<!-- {rel} -->\n' + doc_path.read_text(encoding='utf-8'))
        content = '\n\n---\n\n'.join(docs) if docs else "当前权威文档未找到。"
            
        return {
            'document': 'current_pipeline_runtime_contract',
            'content': content
        }

    @mcp.tool(
        name="maya_list_foreground_sessions",
        annotations={
            "title": "Discover active Maya foreground sessions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def maya_list_foreground_sessions() -> dict:
        """Discover active Maya foreground sessions and their ports.

        MUST be called before ANY foreground Maya operation.
        Returns a list of active commandPort numbers in the configured range.
        Use the returned port in maya_exec_code(foreground_port=<port>).
        Do NOT guess or hardcode port numbers.

        在前台操作 Maya 前必须先调用此工具获取端口。
        """
        from mcp_server.ports import maya_port_range, discover_maya_ports
        rng = maya_port_range()
        start, end = rng.start, rng.stop - 1
        active_ports = discover_maya_ports()
                
        return {
            'status': 'SUCCESS',
            'active_ports': active_ports,
            'scan_range': [start, end],
            'message': f'Found {len(active_ports)} active Maya CommandPort session(s) in range {start}-{end}.',
            'next_step': 'Pass execution_mode="foreground" and foreground_port=<port> to maya_exec_code or named maya_ tools.',
            'open_port_hint': 'Maya: cmds.commandPort(name=":7009", sourceType="python", echoOutput=True)',
        }

    @mcp.tool(
        name="blender_list_foreground_sessions",
        annotations={
            "title": "Discover active Blender foreground sessions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def blender_list_foreground_sessions() -> dict:
        """Discover authenticated CGI Pipeline bridges in open Blender sessions."""
        from dccs.blender.foreground_client import blender_port_range, discover_blender_sessions

        sessions = discover_blender_sessions()
        ports = [session["port"] for session in sessions]
        port_range = blender_port_range()
        return {
            "status": "SUCCESS",
            "active_ports": ports,
            "active_sessions": sessions,
            "scan_range": [port_range.start, port_range.stop - 1],
            "message": f"Found {len(sessions)} authenticated Blender foreground session(s).",
            "next_step": "Pass execution_mode='foreground' and foreground_port=<port> to blender_exec_code.",
        }

    @mcp.tool(
        name="ue_list_foreground_sessions",
        annotations={
            "title": "Discover active Unreal Editor sessions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def ue_list_foreground_sessions() -> dict:
        """Discover Unreal Editors exposing the internal localhost bridge.

        Legacy McpAutomationBridge sessions use 8090-8099. The managed native
        UE_MCP_Bridge publishes its per-project port in
        ``Saved/UE_MCP_Bridge/port.json``. The foreground client safely probes
        each transport with its own protocol and reports ``bridge_protocol``.
        """
        from dccs.ue.foreground_client import discover_ue_sessions, ue_port_range

        sessions = discover_ue_sessions()
        port_range = ue_port_range()
        dynamic_ports = sorted(
            {
                port
                for session in sessions
                for port in session.get("ports", [])
                if session.get("bridge_protocol") == "native"
            }
        )
        return {
            "status": "SUCCESS",
            "active_ports": [session["port"] for session in sessions],
            "active_sessions": sessions,
            "scan_range": [port_range.start, port_range.stop - 1],
            "dynamic_ports": dynamic_ports,
            "dynamic_port_source": "<uproject>/Saved/UE_MCP_Bridge/port.json",
            "bridge_protocols": ["legacy", "native"],
            "message": f"Found {len(sessions)} Unreal Editor foreground session(s).",
            "next_step": "Pass the selected foreground_port to ue_exec_code or ue_exec_action; transport selection is automatic.",
        }
