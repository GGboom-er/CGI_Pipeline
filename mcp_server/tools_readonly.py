# mcp_server/tools_readonly.py
# ── 只读 MCP Tools ──
# 从 server.py 拆分：所有不修改场景数据的查询类 Tool

from mcp_server.models import (
    TaskQueryInput,
    ResolveAssetInput,
    ResolveShotInput,
)
from mcp_server.internals import _read_audit, _SKILLS


def register_readonly_tools(mcp):
    """将所有只读 Tools 注册到 MCP Server 实例"""

    @mcp.tool(
        name="maya_list_skills",
        annotations={
            "title": "列出可用技能",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def maya_list_skills() -> dict:
        """列出所有已注册技能及其参数 Schema。

        只读查询，不启动任何 DCC 进程。
        返回每个技能的 skill_id、名称、所属 DCC（maya/blender）和参数定义。
        在不确定该调用哪个 Tool 时，先调此接口查看系统能力。
        """
        from mcp_server.internals import _SKILLS
        return {'total': len(_SKILLS), 'skills': _SKILLS}


    @mcp.tool(
        name="maya_query_task",
        annotations={
            "title": "查询任务状态",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def maya_query_task(params: TaskQueryInput) -> dict:
        """轮询异步任务的执行状态。

        所有操作类 Tool 提交后都返回 task_id，必须用此接口轮询直到终态。
        状态流转：NOT_FOUND → STARTED/PROGRESS → SUCCESS / ERROR / TIMEOUT / BLOCKED / AUDIT_FAILED / CHAIN_ABORTED
        NOT_FOUND 表示任务尚未被 Worker 拾取，等 3-5 秒重试。
        BLOCKED 表示路径保护拦截。
        AUDIT_FAILED 表示质检或数据契约不通过，后台流程会中止并生成报告。
        """
        return _read_audit(params.task_id)


    @mcp.tool(
        name="maya_resolve_asset",
        annotations={
            "title": "查询资产路径和版本",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def maya_resolve_asset(params: ResolveAssetInput) -> dict:
        """查询资产的最新版本路径和版本列表。只读，不启动 DCC。

        这是操作前的必要步骤：先用此接口获取 source_path，再传给操作类 Tool。
        不传 stage 时按 pipeline 环节优先级自动选择（model: tex>uv>mod, rig: lib>rig>lyrig）。
        传入 task 可定位 sub task 目录（如 bs、paintTex），不传则使用 primary_task。
        返回的 path 字段即可直接作为其他 Tool 的 source_path 参数。
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
            "title": "查询镜头路径和版本",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def maya_resolve_shot(params: ResolveShotInput) -> dict:
        """查询镜头的最新版本路径和版本列表。只读，不启动 DCC。

        用于定位 shot 流程中的工程文件（ly/ani/cfx/efx/lgt/mat 等阶段）。
        不传 stage 时返回该镜头所有可用阶段的信息。
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
            "title": "列出所有可用工作流",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def list_workflows_tool() -> dict:
        """列出所有已注册的工作流及其描述和步骤。

        只读查询，不启动任何 DCC 进程。
        返回每个工作流的 ID、名称、描述和包含的技能步骤列表。
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
            "title": "查询服务和 Worker 心跳",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def pipeline_service_status() -> dict:
        """查询 Redis、Maya/Blender/Workflow Worker 的 PID 与 Celery 队列心跳。"""
        from core.service_manager import get_service_status
        return {
            'status': 'SUCCESS',
            'services': get_service_status(include_heartbeat=True),
        }


    @mcp.tool(
        name="pipeline_explain_architecture",
        annotations={
            "title": "管线架构自证与白皮书查阅",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def pipeline_explain_architecture() -> dict:
        """获取 CGI Pipeline v2 的架构设计白皮书与底层逻辑。
        
        当 AI (你) 对管线的异步执行、沙盒路径、收据结构、异常转译等底层架构产生疑惑，
        或需要深入理解为什么某操作被拦截时，主动调用此工具查阅。
        返回值包含完整的 Markdown 说明。
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
            "title": "扫描活动的前台 Maya 会话",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def maya_list_foreground_sessions() -> dict:
        """扫描本地端口（7001-7010），列出当前所有可用的 Maya 前台会话。
        
        如果用户打开了多个 Maya 实例并分别开启了 commandPort，
        必须先用此工具确认端口号，再在 foreground 调用中显式传 foreground_port。
        """
        import socket
        active_ports = []
        for port in range(7001, 7011):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.2)
                s.connect(('127.0.0.1', port))
                s.close()
                active_ports.append(port)
            except (ConnectionRefusedError, TimeoutError, OSError):
                pass
                
        return {
            'status': 'SUCCESS',
            'active_ports': active_ports,
            'message': f'共发现 {len(active_ports)} 个存活的 Maya CommandPort 会话。',
            'usage': '下一步调用 maya_exec_code/具名 maya_ Tool 时传 execution_mode="foreground" 并显式传 foreground_port。',
            'open_port_hint': 'Maya 端推荐：cmds.commandPort(name=":7009", sourceType="python", echoOutput=True)',
        }
