# mcp_server/server.py
# ── CGI Pipeline MCP Server v3.1 ──
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
# Instructions 动态生成
# ══════════════════════════════════════════════════

_SKILLS = get_all_skills()


def _build_instructions() -> str:
    """从 manifest + registry 动态生成 MCP instructions"""
    m = get_manifest()
    principles = m.get('principles', {})
    status_codes = m.get('status_codes', {})
    safety_rules = m.get('exec_code_safety', [])
    chain_rules = m.get('chain_rules', {})

    readonly_drives = ', '.join(d.upper() + ':/' for d in principles.get('readonly_drives', []))
    unc_prefixes = ', '.join(principles.get('readonly_unc_prefixes', []))
    status_str = ' | '.join(f'{k}={v}' for k, v in status_codes.items())
    safety_str = '\n'.join(f'- {r}' for r in safety_rules)
    skill_ids = ', '.join(s['skill_id'] for s in _SKILLS)

    return (
        "CGI/VFX 管线自动化服务器。通过 Tool 控制 Maya/Blender 执行资产处理。\n"
        "\n"
        "## 操作流程（所有操作类 Tool 都是异步的）\n"
        "1. 调用操作 Tool（如 maya_clean_skinweights）→ 返回 task_id\n"
        "2. 用 maya_query_task(task_id) 轮询 → 等 status 变为 SUCCESS/ERROR\n"
        "3. 从 detail 字段读取执行结果\n"
        "轮询间隔建议 3-5 秒。首次查询可能返回 NOT_FOUND（任务尚未开始），等几秒重试即可。\n"
        "\n"
        "## 核心存储架构与沙盒规则 (Sandbox Architecture)\n"
        f"- {readonly_drives} 是生产服务器映射盘，严格只读，禁止保存任何文件或用于自动化落盘测试。\n"
        f"- UNC 路径（{unc_prefixes}）同样严格只读。\n"
        "- 【自动沙盒隔离原则】：管线底层引擎已集成全自动防覆盖机制。当 AI 传入 `X:/Project/...` 等受保护的源路径时，系统会自动在 `projects/{project_name}/...` 内创建任务级的专属沙盒目录（例如 `projects/ysj/{datetime}_{asset}_{wf_id}`），并将源文件隔离其中。\n"
        "- AI 不需要手动构造任何沙盒路径。请直接传入解析到的原生真实路径（如 `X:/Project/...`），所有输入、输出和报告均由系统底层在任务沙盒内闭环完成。\n"
        "- 若主动指定 save_scene，其保存路径也必须在工作区目录内，违反路径保护的操作会被自动拦截并返回 BLOCKED 状态。\n"
        "- 获取 source_path：先调 maya_resolve_asset 查到原生路径，直接传给操作 Tool 即可。\n"
        "\n"
        "## Tool 选择策略\n"
        "- 所有标准技能现已自动注册为具名 Tool（例如 maya_clean_skinweights, blender_export_abc 等），直接调用具名 Tool 即可，参数已强类型化。\n"
        "- 若因特殊原因无法使用具名 Tool，可用 execute_skill（兜底接口）。\n"
        "- 需要多步连续操作 → 用 maya_execute_chain（一次提交，共享 DCC 会话）\n"
        "- 临时查询或一次性脚本 → 用 maya_exec_code\n"
        "- 当前已打开 Maya 场景 → 用 maya_exec_code 或具名 maya_ Tool，必须传 execution_mode=\"foreground\" 和显式 foreground_port。端口未知先调 maya_list_foreground_sessions，禁止省略端口或依赖默认值。\n"
        "\n"
        "## 链式执行规范（maya_execute_chain）\n"
        "- 链引擎会自动打开 source_path，技能步骤中不需要再传 source_path。\n"
        "- **同一后台极速流转**：链式执行会复用同一个后台 Maya 进程。如果用户要求“在同一任务执行多次对比与同步”，必须将同属 Maya 的技能（如 `maya_compare_mesh_topology` 和 `maya_sync_rig_incremental`）打包在一个 `maya_execute_chain` 任务内完成！\n"
        f"- save_scene 必须放在链的最后一步（{'强制' if chain_rules.get('save_scene_must_be_last') else '建议'}）\n"
        f"- 任何步骤失败，链会中断并返回 CHAIN_ABORTED（{'强制' if chain_rules.get('abort_on_step_failure') else '可选'}）\n"
        "- 步骤返回 AUDIT_FAILED/BLOCKED/ERROR 时链会中止并生成报告；后台流程不等待人工继续。\n"
        "\n"
        "## exec_code 规范\n"
        "代码中必须将结果赋值给 result 变量（dict 类型），示例：\n"
        "  import maya.cmds as cmds\n"
        "  result = {'status': 'SUCCESS', 'meshes': cmds.ls(type='mesh')}\n"
        "\n"
        "## exec_code 安全红线\n"
        f"{safety_str}\n"
        "\n"
        "## 状态码\n"
        f"{status_str}\n"
        "\n"
        f"已注册技能：{skill_ids}。用 maya_list_skills 查看完整参数。"
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
    if '--http' in sys.argv:
        host = os.getenv('MCP_HOST', '0.0.0.0')
        port = int(os.getenv('MCP_PORT', '8000'))
        print(f'[cgi_pipeline_mcp] Streamable HTTP on http://{host}:{port}/mcp')
        print(f'[cgi_pipeline_mcp] {len(_SKILLS)} skills registered')
        mcp.run(transport='streamable-http', host=host, port=port)
    else:
        mcp.run(transport='stdio')
