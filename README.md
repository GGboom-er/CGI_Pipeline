# CGI Pipeline v2.0

自动化 DCC 管线系统 —— 通过 AI (Claude/Gemini) 自然语言指令驱动 Maya/Blender 批处理任务。

## 架构

```text
AI (Claude/Gemini)
  ├──► cgi-pipeline MCP (FastMCP 工业管线调度)
  │      ├─ execution_mode: "background" ─► Celery 队列 ─► DCC Worker (无头后台)
  │      └─ execution_mode: "foreground" ─► Socket 直连 ─► DCC 当前活跃前台实例
  │
  └──► maya-live MCP (CommandPort 实时后门)
         └─► 直连 Maya 前台 REPL 执行纯代码问答与交互
```

> **注意：** `cgi-pipeline` 的执行结果统一流经 Redis 并在本地落盘审计日志（`audit/`），全程支持基于 `task_id` 的异步追踪。

### 核心执行模式

| 模式 | 路由参数 `execution_mode` | 行为说明 |
|------|---------------------------|----------|
| 自动化后台批处理 | `"background"` (默认) | 将任务发给 Celery 调度，拉起 `mayapy` 或无头 Blender 进行静默处理。全程不阻塞用户当前界面。 |
| 当前前台实例处理 | `"foreground"` | 自动嗅探本地 7001-7010 端口，直接把任务 Payload 塞给活着的 Maya/Blender 实例执行。响应极快。 |

工作流引擎 (`execute_chain` / `execute_workflow`) 按 skill 的 `dcc` 属性自动分段：
- 同 DCC 的连续步骤合并为一段（共享 DCC 会话）
- 段间通过 `{{outputs.step_id.field}}` 模板变量传递数据
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
python -m celery -A core.tasks worker -Q dcc_queue --pool=solo -c 1 -l info
# Blender Worker
python -m celery -A core.tasks worker -Q blender_queue --pool=solo -c 1 -l info
```

服务管理器 (`core/service_manager.py`) 会自动检测并拉起 Redis 和 Worker。

## 新项目接入

换项目只需 2 步，**零代码修改**：

1. 复制 `config/ysj_config.json` → `config/{新项目名}_config.json`
2. 修改 `asset_root`、`categories`、`stages` 规则

路径解析全由 `config/path_templates.json` 模板 + 项目配置驱动。

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
├── docs/               # 文档归档
├── redis_server/       # 内嵌 Redis 可执行文件
├── .env.example        # 环境变量模板
└── environment.yml     # Conda 环境定义
```

运行时目录（不提交 Git）：`audit/` `ipc/` `runtime/` `logs/` `reports/` `runs/` `projects/`

## 技能清单

### Maya 技能
| skill_id | 功能 |
|----------|------|
| `maya_master_cleanup` | 管线全自动清理（check/fix 模式） |
| `maya_clean_skinweights` | 蒙皮噪声权重清理 |
| `maya_fix_shape_names` | Shape/Orig 命名规范修复 |
| `maya_freeze_transforms` | 冻结变换归零 |
| `maya_conform_normals` | 统一法线 + 清零 pnts |
| `maya_import_abc` | Maya 原生 ABC 导入 |
| `maya_export_abc` | Maya ABC 导出 |
| `maya_build_mesh_from_abc` | PyAlembic 纯数据构建 mesh（含 UV） |
| `maya_apply_materials` | 消费 _materials.json 按面赋予材质 |
| `maya_build_asset_info` | 从标准 ShapeOrig 采集 rig/mesh 几何 _info.json |
| `maya_compare_asset_in_scene` | 当前 Maya rig 场景内采集并与 source ABC/_info 对比 |
| `maya_sync_rig_incremental` | 消费前置 compare_result 增量同步拼装（ABC→rig） |
| `maya_compare_mesh_topology` | mesh 拓扑对比 |
| `maya_check_textures` | 贴图路径检查 |
| `maya_split_udim_materials` | UDIM 材质按象限拆分 |
| `maya_assign_udim_materials` | UDIM 材质赋予 |
| `check_uvsets` | UV 集检查 |
| `simplify_uvsets` | UV 集精简 |
| `save_scene` | 保存/另存场景 |
| `exec_code` | Maya 任意代码执行 |

### Blender 技能
| skill_id | 功能 |
|----------|------|
| `blender_export_abc` | Blender 导出 ABC + FaceSet |
| `blender_extract_materials` | 采集 per-face 材质信息 JSON |
| `blender_build_asset_info` | 采集 mesh 拓扑/材质信息 |
| `blender_exec_code` | Blender 任意代码执行 |

### Pipeline 技能（纯计算，不需要 DCC）
| skill_id | 功能 |
|----------|------|
| `pipeline_compare_asset` | 纯 JSON/ABC 资产对比，输出 compare_result |
| `pipeline_export_abc_auto` | 自动路由 ABC 导出 |

### 工具技能
| skill_id | 功能 |
|----------|------|
| `ping` | 连通性测试 |
| `copy_files` | 文件拷贝 |
| `rename_asset` | 资产重命名 |
| `validate_publish` | 发布前 QC 门禁 |

## 工作流清单（8 个）

| workflow_id | 说明 | 跨 DCC |
|-------------|------|--------|
| `blender_to_maya_full_build` | Blender 导出 → Maya 构建 + 材质 + 保存 | Blender→Maya |
| `tex_to_rig_verify_and_sync` | Blender 导 ABC/材质 → Maya 场景内对比 → compare_result 驱动同步 → 后置验证 → 升版本保存 | Blender→Maya |
| `tex_to_rig_verify` | Blender 导 ABC → Maya 场景内采集 target → 写 compare_result | Blender→Maya |
| `blender_tex_export` | Blender 导出 ABC + 材质 + info | Blender |
| `abc_import_with_materials` | PyAlembic 构建 + 材质赋予 | Maya |
| `full_cleanup_and_save` | 全清理 + Shape 修复 + 法线 + 保存 | Maya |
| `rig_full_cleanup` | 权重清理 + Shape + 全清理 + 保存 | Maya |
| `qc_and_publish` | QC 检查 + 发布门禁 | Maya |

## 许可

内部工具，仅供团队使用。
