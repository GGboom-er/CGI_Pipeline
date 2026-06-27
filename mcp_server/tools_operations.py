# mcp_server/tools_operations.py
# ── 操作类 MCP Tools ──
# 从 server.py 拆分：所有修改场景数据或触发执行的 Tool

import sys
import os
import socket
from pathlib import Path
from fastmcp.tools import Tool

from mcp_server.models import (
    ExecCodeInput,
    ExecuteChainInput,
    ExecuteSkillInput,
    StartWorkerInput,
    CopyFilesInput,
    CompareAssetInput,
    SyncRigAssetInput,
    ExecuteWorkflowInput,
    create_skill_model,
)
from mcp_server.internals import (
    _submit_to_celery,
    _submit_chain,
    _submit_workflow,
    _ensure_worker,
    _is_pid_alive,
    _SKILL_MAP,
    PROJECT_ROOT,
)

from core.skill_registry import get_all_skills, get_skill_map


SKILL_TIERS = {'read', 'write', 'destructive'}


# 端口扫描抽到共享叶子模块 mcp_server.ports（消除 tools_readonly/tools_operations/foreground_client 三处重复）
from mcp_server.ports import discover_maya_ports


def _require_explicit_foreground_port(params, tool_name):
    """foreground 模式必须显式传端口，避免多 Maya 实例时误连默认端口。"""
    if getattr(params, 'execution_mode', 'background') != 'foreground':
        return None
    if 'foreground_port' in getattr(params, 'model_fields_set', set()):
        return None
    return {
        'status': 'NEEDS_ATTENTION',
        'tool': tool_name,
        'error': 'foreground 模式必须显式传 foreground_port，避免多 Maya commandPort 会话时误连。',
        'active_ports': discover_maya_ports(),
        'recovery_hint': '先用 maya_list_foreground_sessions 确认端口，再显式传 foreground_port。Maya 端建议开启：cmds.commandPort(name=":7009", sourceType="python", echoOutput=True)',
    }


def _skill_tool_annotations(skill: dict) -> dict:
    """Build MCP annotations from SKILL.md frontmatter tier."""
    skill_id = skill.get('skill_id', 'unknown_skill')
    tier = str(skill.get('tier') or 'destructive').strip().lower()
    if tier not in SKILL_TIERS:
        tier = 'destructive'

    is_read = tier == 'read'
    is_destructive = tier == 'destructive'
    return {
        "title": skill.get('name', skill_id),
        "readOnlyHint": is_read,
        "destructiveHint": is_destructive,
        "idempotentHint": is_read,
        "openWorldHint": True,
    }


def register_operation_tools(mcp):
    """将所有操作类 Tools 注册到 MCP Server 实例"""

    # ── 动态注册所有标准技能 Tools ──
    from core.skill_registry import get_all_skills
    
    # ── 白名单式暴露 ──
    # 只有 SKILL.md frontmatter 显式声明 mcp_expose: true 的技能才注册为具名 MCP 工具。
    # 新增 skill 默认不暴露（需显式开启），符合最小暴露原则；未暴露的技能仍可经
    # execute_skill(skill_id=...) 兜底调用，能力无损。不声明 mcp_expose 的常见原因：
    # 有专属手动 Tool / 低频可被 exec_code 替代 / 被更高级工具覆盖 / 仅内部或 workflow 调用。
    
    for skill in get_all_skills():
        skill_id = skill.get('skill_id')
        if not skill_id or not skill.get('mcp_expose', False):
            continue
            
        # 动态创建 Input Model
        InputModel = create_skill_model(skill)
        
        # 闭包捕获，避免循环变量泄漏
        def make_tool_func(sid, s_desc):
            async def dynamic_tool(params: InputModel) -> dict:
                guard = _require_explicit_foreground_port(params, sid)
                if guard:
                    return guard

                # 提取 parameters (排除 _SkillInput 的基础字段)
                raw_params = params.model_dump()
                p_project = raw_params.pop('project', 'default')
                p_asset = raw_params.pop('asset_name', 'untitled')
                p_source = raw_params.pop('source_path', '')
                
                return _submit_to_celery(sid, {
                    'project': p_project,
                    'asset_name': p_asset,
                    'source_path': p_source,
                    'parameters': raw_params,
                })
            
            dynamic_tool.__name__ = sid
            dynamic_tool.__doc__ = s_desc
            return dynamic_tool
            
        desc = skill.get('description', f"Execute skill {skill_id}. Returns task_id (async).")
        tool_fn = make_tool_func(skill_id, desc)
        
        tool_obj = Tool.from_tool(
            tool_fn,
            name=skill_id,
            description=desc,
            annotations=_skill_tool_annotations(skill),
        )
        mcp.add_tool(tool_obj)


    # ── 通用技能执行 ──

    @mcp.tool(
        name="execute_skill",
        annotations={
            "title": "执行任意已注册技能（Maya/Blender 通用）",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def execute_skill(params: ExecuteSkillInput) -> dict:
        """通用技能执行入口（兜底接口）。

        异步执行，返回 task_id。优先使用具名 Tool（如 maya_clean_skinweights）。
        仅在调用没有独立 Tool 的技能时使用，如 fix_shape_names、udim_material_split、
        import_abc、assign_udim_materials、blender_export_abc 等。
        支持 Maya 和 Blender 技能，DCC 类型由 skill 注册信息自动推断。
        用 maya_list_skills 查看所有可用 skill_id 及其参数。
        """
        from mcp_server.internals import _SKILL_MAP
        guard = _require_explicit_foreground_port(params, 'execute_skill')
        if guard:
            return guard

        # exec_code / blender_exec_code 有专属具名工具，兜底入口挡掉，避免同一能力多入口
        _NAMED_TOOL_FOR = {'exec_code': 'maya_exec_code', 'blender_exec_code': 'blender_exec_code'}
        if params.skill_id in _NAMED_TOOL_FOR:
            named = _NAMED_TOOL_FOR[params.skill_id]
            return {
                'status': 'ERROR',
                'error': f'技能 "{params.skill_id}" 有专属具名工具，请直接用 {named}；execute_skill 仅兜底无具名工具的技能。',
                'use_tool': named,
                'recovery_hint': f'改调用 {named}。',
            }

        if params.skill_id not in _SKILL_MAP:
            return {
                'status': 'ERROR',
                'error': f'未知技能 "{params.skill_id}"',
                'available_skills': list(_SKILL_MAP.keys()),
                'recovery_hint': '使用 maya_list_skills 查看所有可用技能。',
            }
        p = params.parameters or {}
        p['execution_mode'] = params.execution_mode
        p['foreground_port'] = params.foreground_port
        
        return _submit_to_celery(params.skill_id, {
            'project': params.project,
            'asset_name': params.asset_name,
            'source_path': params.source_path,
            'parameters': p,
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
        if params.dcc not in ("maya", "blender", "workflow", "pipeline"):
            return {"status": "ERROR", "message": f"暂不支持启动 {params.dcc} worker"}

        dcc = 'maya' if params.dcc == 'pipeline' else params.dcc
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
        if params.dcc not in ("maya", "blender", "workflow", "pipeline"):
            return {"status": "ERROR", "message": f"暂不支持重启 {params.dcc} worker"}
        dcc = 'maya' if params.dcc == 'pipeline' else params.dcc
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
        name="maya_execute_chain",
        annotations={
            "title": "链式批处理（多技能顺序执行）",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def maya_execute_chain(params: ExecuteChainInput) -> dict:
        """一次性提交多步技能链，在同一个 DCC 会话中顺序执行。支持 Maya 和 Blender。

        异步执行，返回 task_id。链引擎自动打开 source_path，步骤中不需要再传 source_path。
        DCC 类型由第一个技能的注册信息自动推断（Maya 技能 → Maya 进程，Blender 技能 → Blender 进程）。
        链内所有技能必须属于同一个 DCC，混合链会被拒绝。

        关键规则：
        - save_scene 必须放在链的最后一步（Maya 链）
        - 任何步骤失败 → 链中断，返回 CHAIN_ABORTED
        - 步骤返回 AUDIT_FAILED/BLOCKED/ERROR → 链中止并生成报告；后台流程不等待人工继续

        Maya 链典型用法：clean_skinweights → fix_shape_names → save_scene
        Blender 链典型用法：blender_export_abc → blender_build_asset_info
        """
        guard = _require_explicit_foreground_port(params, 'maya_execute_chain')
        if guard:
            return guard

        chain_data = [{'skill_id': s.skill_id, 'parameters': s.parameters} for s in params.skill_chain]
        return _submit_chain({
            'source_path': params.source_path,
            'project': params.project,
            'asset_name': params.asset_name,
            'skill_chain': chain_data,
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
        DCC 源文件请先通过对应采集/导出 skill 转换为 _info.json 或 .abc。
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
        `compare_result` 必须来自前置对比 skill。
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
        """执行指定工作流。支持跨 DCC 编排（Blender + Maya + pipeline 混合）。

        异步执行，返回 task_id。工作流引擎自动按 DCC 类型分段执行，
        段间传递输出（通过 {{outputs.step_id.field}} 模板变量）。

        典型用法：
        - blender_tex_export: Blender 导出 ABC + 采集 info
        - tex_to_rig_verify: 资产名/显式路径 → Blender source → Maya rig 校验（跨 DCC）
        - tex_to_rig_verify_and_sync: 资产名/显式路径 → 对比 → 拼装 → 材质 → 保存
        - rig_full_cleanup: 蒙皮清理 → Shape 修复 → 全清理 → 保存
        - abc_import_with_materials: ABC 导入 + UDIM 材质分配
        """
        return _submit_workflow({
            'workflow_id': params.workflow_id,
            'source_path': params.source_path,
            'project': params.project,
            'asset_name': params.asset_name,
            'extra_params': params.extra_params or {},
        })


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
        """热重载 MCP Server 业务模块，使 core/ 和 skills/ 的代码修改立即生效。

        清除已缓存的 Python 模块，移除并重新注册所有 Tool handler（替换旧闭包），
        使 tools_readonly/tools_operations 改动对客户端立即生效。不会中断 MCP 连接。
        """
        import importlib
        reloaded = []
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith(('core.', 'skills.', 'mcp_server.', 'dccs.')):
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
            tools_after = await mcp.list_tools()
            re_registered = len(tools_after)
        except Exception as e:
            return {
                "status": "PARTIAL",
                "reloaded_modules": reloaded,
                "error": f"模块 reload 成功但 Tool re-register 失败: {e!r}",
                "message": "需要彻底重启 MCP server 进程",
            }

        from mcp_server.internals import _SKILLS
        return {
            "status": "RELOADED",
            "reloaded_modules": reloaded,
            "skills_count": len(_SKILLS),
            "tools_reregistered": re_registered,
            "message": f"已热重载 {len(reloaded)} 个模块，重注册 {re_registered} 个 Tool，{len(_SKILLS)} 个技能",
        }
