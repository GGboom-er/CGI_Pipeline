# CGI Pipeline v2.0

自动化 DCC 管线系统 —— 通过 AI (Claude/Gemini) 自然语言指令驱动 Maya/Blender 批处理任务。

## AI 会话记忆

所有 AI 会话、上下文恢复和任务接手前，必须先读取 [AGENTS.md](AGENTS.md)，再按顺序读取 [docs/ai_startup](docs/ai_startup/) 固定必读包。该目录集中维护启动读取顺序、问题闭环协议、项目基石、知识库和文档索引，避免规则散落在聊天记录或多个旧路径里。

## 架构

```text
AI (Claude/Gemini)
  ├──► cgi-pipeline MCP (FastMCP 工业管线调度)
  │      ├─ execution_mode: "background" ─► Celery 队列 ─► DCC Worker (无头后台)
  │      └─ execution_mode: "foreground" + foreground_port ─► 当前指定 Maya 前台实例
```

> **注意：** `cgi-pipeline` 的执行结果统一流经 Redis 并在本地落盘审计日志（`audit/`），全程支持基于 `task_id` 的异步追踪。

### 核心执行模式

| 模式 | 路由参数 `execution_mode` | 行为说明 |
|------|---------------------------|----------|
| 自动化后台批处理 | `"background"` (默认) | 将任务发给 Celery 调度，拉起 `mayapy` 或无头 Blender 进行静默处理。全程不阻塞用户当前界面。 |
| 当前前台实例处理 | `"foreground"` | 必须显式传 `foreground_port`，直接把任务 Payload 发给指定 Maya commandPort。省略端口会返回 `NEEDS_ATTENTION`，避免多 Maya 会话误连。 |

### 当前 Maya 场景访问 SOP

当用户要求查看或修改已经打开的 Maya 场景时，AI 必须优先使用 `cgi-pipeline` MCP 的 `maya_exec_code` 或具名 `maya_` Tool，并显式传入：

```json
{
  "execution_mode": "foreground",
  "foreground_port": 7009,
  "sync": true
}
```

如果通过原始 Python MCP Client 调用 FastMCP，`maya_exec_code` 这类工具的入参外层是 `{"params": {...}}`，不要把 `code/execution_mode/foreground_port` 直接平铺到 `call_tool` 顶层。

禁止依赖旧的 `maya-live`、默认端口或省略 `foreground_port`。如果不知道端口，先调用 `maya_list_foreground_sessions` 查看活动端口，或询问用户当前打开的是哪个端口。默认扫描 `7001-7020`，可用环境变量 `MAYA_FOREGROUND_PORT_START` / `MAYA_FOREGROUND_PORT_END` 覆盖。

Maya 端推荐开启方式：

```python
import maya.cmds as cmds

if cmds.commandPort(":7009", q=True):
    cmds.commandPort(name=":7009", close=True)

cmds.commandPort(name=":7009", sourceType="python", echoOutput=True)
```

工作流引擎 (`execute_chain` / `execute_workflow`) 按 skill 的 `dcc` 属性自动分段：
- 同 DCC 的连续步骤合并为一段（共享 DCC 会话）
- 段间通过 `{{outputs.step_id.field}}` 模板变量读取上游标准执行记录的 `output.field`
- 支持断点恢复（Redis 持久化已完成段 + outputs）

## 快速开始

### 前置条件

- **Windows 10/11**
- **Maya 2025**（含 mayapy）
- **Blender 4.x**（后台模式）
- **Miniconda / Anaconda**（Python 3.11 环境）
- **Redis**（内嵌 `redis_server/redis-server.exe`，自动启动）

### 一键部署

```batch
git clone <仓库地址>
cd CGI_Pipeline
bin\setup.bat
```

部署脚本会自动：
1. 从 `environment.yml` 创建 `cgi_pipeline` conda 环境（Python 3.11）
2. 安装所有 Python 依赖
3. 创建运行时目录结构
4. 从模板生成 `.env` 配置文件

### 手动配置

部署完成后，编辑 `.env` 文件修改以下配置：

```env
PROJECT_ROOT=<你的项目路径>
MAYAPY_PATH=<Maya 2025 mayapy.exe 完整路径>
BLENDER_PATH=<Blender 可执行文件路径>
```

## 启动服务

```powershell
# 1. 激活环境
conda activate cgi_pipeline

# 2. 启动 Dashboard（含 MCP Server + 自动拉起 Redis/Worker）
python -m dashboard.app

# 或手动分别启动：
# Redis（自动内嵌，通常无需手动）
# Maya Worker
python -m celery -A core.tasks worker -Q dcc_queue --pool=solo -c 1 -l info --hostname=cgi_maya@%h
# Blender Worker
python -m celery -A core.tasks worker -Q blender_queue --pool=solo -c 1 -l info --hostname=cgi_blender@%h
# Workflow Worker
python -m celery -A core.tasks worker -Q workflow_queue --pool=solo -c 1 -l info --hostname=cgi_workflow@%h
```

服务管理器 (`core/service_manager.py`) 会自动检测并拉起 Redis 和 Worker。提交任务前会同时检查 PID 和 Celery 队列心跳；心跳窗口默认 5 秒，PID 存活但心跳丢失时会自动重启对应 Worker。MCP 侧可用 `pipeline_service_status` 查看心跳，用 `pipeline_restart_worker` 手动重启。

## CLI 工作流

```powershell
# 只传资产名：workflow 内部解析最新 tex/rig 文件
python cli.py run-workflow tex_to_rig_verify_and_sync --project ysj --asset ciweiguai

# 显式指定文件：解析节点只校验并透传这两个路径
python cli.py run-workflow tex_to_rig_verify_and_sync --project ysj --asset ciweiguai --source-path X:/Project/ysj/pub/assets/chr/ciweiguai/tex/texMaster/xxx.blend --rig-path X:/Project/ysj/pub/assets/chr/ciweiguai/rig/rigMaster/xxx.ma
```

## 新项目接入

换项目只需 2 步，**零代码修改**：

1. 复制 `config/ysj_config.json` → `config/{新项目名}_config.json`
2. 修改 `asset_root`、`categories`、`stages` 规则

路径解析由 `core/asset_resolver.py` + 项目配置驱动。

## 项目结构

```
CGI_Pipeline/
├── mcp_server/         # MCP 意图接口层（FastMCP server）
├── core/               # 核心引擎层
│   ├── tasks.py        #   Celery 任务定义（链式引擎 + 工作流引擎）
│   ├── skill_registry.py #   技能注册表（动态扫描 SKILL.md）
│   ├── service_manager.py # 服务生命周期管理
│   ├── manifest.py     #   系统宪法加载器
│   ├── abc_reader.py   #   PyAlembic 读取器
│   ├── path_guard.py   #   路径保护（只读盘检测）
│   └── receipt.py      #   标准回执构造器
├── dccs/               # DCC 集成层（maya/, blender/）
├── skills/             # 技能库（每个技能一个文件夹）
│   └── {skill_id}/
│       ├── {skill_id}.py   # 实现（execute 入口）
│       ├── SKILL.md        # 元数据（YAML frontmatter）
│       └── __init__.py     # 导出 execute
├── workflows/          # 工作流定义（JSON）
├── config/             # 配置中心（项目规则 + 路径模板 + 管线宪法）
├── dashboard/          # Web 监控面板（FastAPI + SSE）
├── tests/              # 测试脚本
├── bin/                # 部署与打包脚本
├── docs/               # 项目基石、当前知识库、运行规范和专项文档
├── redis_server/       # 内嵌 Redis 可执行文件
├── .env.example        # 环境变量模板
└── environment.yml     # Conda 环境定义
```

运行时目录（不提交 Git）：`audit/` `ipc/` `runtime/` `logs/` `reports/` `runs/` `projects/`

## 技能入口

当前注册技能以运行时扫描结果为准，不在 README 维护完整手抄清单：

```powershell
python cli.py list-skills
```

主 workflow 相关核心技能：

| skill_id | 功能 |
|---|---|
| `resolve_asset_files` | 解析资产 tex/rig 最新文件，或校验并透传显式路径 |
| `blender_export_abc` | Blender cache 导出 ABC + FaceSet |
| `blender_extract_materials` | 采集 per-face 材质信息 JSON |
| `maya_check_asset_hierarchy` | 检查 rig/cache 标准层级和顶层异常 |
| `maya_fix_asset_hierarchy` | 按检查结果迁移 legacy `|*|geo` 到 `|Group|Geometry|RIG_geo` |
| `maya_compare_asset_in_scene` | 当前 Maya rig 场景内采集并与 source ABC/_info 对比 |
| `maya_sync_rig_incremental` | 消费前置 compare_result 增量同步拼装 |
| `maya_fix_shape_names` | Shape/Orig 命名规范修复 |
| `maya_apply_materials` | 消费 `_materials.json` 按面赋予材质 |
| `save_scene` | 沙盒内升版本保存 |

新增或改造 skill 的唯一规范是 `skills/build_pipeline_skill/SKILL.md`；具体 skill 的实时参数以 `skills/{skill_id}/SKILL.md` 为准。

## 工作流清单（8 个）

| workflow_id | 说明 | 跨 DCC |
|-------------|------|--------|
| `blender_to_maya_full_build` | Blender 导出 → Maya 构建 + 材质 + 保存 | Blender→Maya |
| `tex_to_rig_verify_and_sync` | 资产名/显式路径解析 → Blender 导 ABC/材质 → Maya 场景内对比 → compare_result 驱动同步 → 后置验证 → 升版本保存 | Pipeline→Blender→Maya |
| `tex_to_rig_verify` | 资产名/显式路径解析 → Blender 导 ABC → Maya 场景内采集 target → 写 compare_result | Pipeline→Blender→Maya |
| `blender_tex_export` | Blender 导出 ABC + 材质 + info | Blender |
| `abc_import_with_materials` | PyAlembic 构建 + 材质赋予 | Maya |
| `full_cleanup_and_save` | 权重清理 + 全清理 + Shape 修复 + 法线 + 保存 | Maya |
| `qc_and_publish` | QC 检查 + 发布门禁 | Maya |

## 许可

内部工具，仅供团队使用。
