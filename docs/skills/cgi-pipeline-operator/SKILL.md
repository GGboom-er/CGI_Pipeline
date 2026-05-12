---
name: cgi-pipeline-operator
description: 使用 CGI Pipeline MCP 对 Maya/Blender 场景进行自动化资产处理的完整工作指南。当用户提到清理蒙皮权重、批处理 Maya 文件、检查层级规范、导出 ABC、执行 Maya 代码、Blender 导出、管线自动化等任务时触发此 skill。也适用于用户说"帮我处理这个文件/角色/资产"等模糊需求。
---

# CGI Pipeline Operator

使用 `cgi_pipeline_mcp` MCP server 对 Maya/Blender 场景进行自动化资产处理的操作指南。

## 🔴 核心限制 (CRITICAL CONSTRAINTS)

你拥有两类工具：

1. **原生文件工具**（list_dir, find_by_name, view_file 等）— 用于定位资产、读取配置
2. **MCP Maya 工具**（maya_ 前缀）— 用于 Maya 场景内部操作

文件系统操作用原生工具，Maya 操作用 MCP 工具。不要混淆。

## 🟢 核心逻辑 (CORE LOGIC)

### 路径映射规则

资产目录在不同环境下根路径不同，但相对结构完全一致：

| 环境 | 根路径 | 用途 |
|---|---|---|
| 测试环境 | `Y:/GGbommer/scripts/CGI_Pipeline/projects/` | 本地开发测试 |
| 生产环境 | `X:/Project/` | 正式服务器资产 |

### 读写分离规则

AI 读取源文件从 `pub` 目录，处理完后保存到 `runs` 目录：

| 操作 | publish_stage | 示例路径 |
|---|---|---|
| 读取源文件 | `pub` | `{root}/{project}/pub/assets/{type}/{asset}/{dept}/{task}/` |
| AI 保存结果 | `runs` | `{root}/{project}/runs/assets/{type}/{asset}/{dept}/{task}/` |

项目配置 `config/{project}_config.json` 中：
- `source_root` — 指向 `pub/assets` 层级（读取源）
- `ai_publish_root` — 指向 `runs/assets` 层级（AI 写入目标）

`pub` 路径在 `protected_roots` 中受保护，`save_scene` 会自动推荐 `runs` 路径。

相对路径结构（读写两侧一致）：
```
{root}/
└── {project}/                        # 项目代号，如 ysj
    ├── pub/assets/                   # 人工发布（只读源）
    │   └── {type}/{asset}/{dept}/{task}/{project}_{type}_{asset}_{dept}_{task}_v{NNN}.ma
    └── runs/assets/                # AI 处理结果（写入目标）
        └── {type}/{asset}/{dept}/{task}/{project}_{type}_{asset}_{dept}_{task}_v{NNN}.ma
```

示例完整路径：
- 读取：`X:/Project/ysj/pub/assets/chr/xiaotianquan/rig/rigMaster/ysj_chr_xiaotianquan_rig_rigMaster_v003.ma`
- 保存：`X:/Project/ysj/runs/assets/chr/xiaotianquan/rig/rigMaster/ysj_chr_xiaotianquan_rig_rigMaster_v003.ma`

**定位资产的流程**：
1. 用 `list_dir` 浏览 `projects/` 确认项目名
2. 用 `find_by_name` 搜索 `.ma` 文件
3. 或用 `maya_resolve_asset` MCP tool 自动解析

## 🔵 核心代码与扩展 (IMPLEMENTATION)

### 观察类（只读）
| Tool | 用途 |
|---|---|
| `maya_list_skills` | 列出所有已注册技能和参数 |
| `maya_query_task` | 查询异步任务执行状态和结果 |
| `maya_resolve_asset` | 查询资产路径和版本（支持 pipeline 参数限定 model/rig 环节） |
| `maya_resolve_shot` | 查询镜头路径和版本（ly/ani/cfx/efx/lgt/mat 等阶段） |
| `list_workflows` | 列出所有已注册工作流及其步骤 |
| `compare_asset` | 纯 JSON 资产对比 + KDTree 几何匹配 + MD 报告 |

### 操作类（修改场景）
| Tool | 用途 |
|---|---|
| `maya_clean_skinweights` | 清理蒙皮噪声权重 |
| `maya_freeze_transforms` | 冻结变换归零 |
| `export_abc` | 导出 Alembic 缓存（自动路由 Maya/Blender） |
| `maya_master_cleanup` | 管线级 Maya 场景全自动清理 |
| `maya_fix_shape_names` | 修复 Shape/Orig 命名规范 |
| `maya_validate_publish` | 发布前 QC 门禁（只读） |
| `execute_skill` | 执行任意已注册技能（Maya/Blender 通用兜底） |

### Blender 相关
| Tool | 用途 |
|---|---|
| `execute_skill` + `blender_export_abc` | Blender 后台导出 ABC + FaceSet |
| `execute_skill` + `blender_build_asset_info` | 采集 Blender 场景 mesh 几何拓扑信息 |
| `export_abc`（传 .blend 路径） | 自动路由到 Blender 导出 ABC |

### 高级
| Tool | 用途 |
|---|---|
| `maya_exec_code` | 在 Maya 中执行任意 Python 代码 |
| `maya_execute_chain` | 一次提交多步技能链式批处理 |
| `execute_workflow` | 执行预定义工作流（跨 DCC 编排） |

## 🟡 参数规则 (PARAMETERS)

### 模式 A：单技能执行

适用于单个操作（如"清理权重"）。

```
1. 定位文件（原生工具或 maya_resolve_asset）
2. 调用对应 maya_ tool
3. 用 maya_query_task 轮询结果（每 3-5 秒一次）
4. 返回结果给用户
```

### 模式 B：链式批处理（推荐）

适用于多步操作（如"清理权重 → 清理无用影响 → 保存"）。

```
1. 定位文件
2. 组装 skill_chain
3. 调用 maya_execute_chain 一次性提交
4. 轮询结果
5. 汇总每步执行报告
```

chain 模式下所有步骤共享同一个 Maya 会话，比逐个调用快 5-10 倍。

### 模式 C：临时代码执行

适用于查询场景信息或一次性自定义操作。

```python
# 示例：查询场景中所有 file 节点的贴图路径
code = """
import maya.cmds as cmds
import json
files = cmds.ls(type='file') or []
textures = []
for f in files:
    path = cmds.getAttr(f + '.fileTextureName') or ''
    if path:
        textures.append({'node': f, 'path': path})
result = {'status': 'SUCCESS', 'count': len(textures), 'textures': textures}
"""
```

代码必须将结果赋值给 `result` 变量（dict，含 status 字段）。

## 重要注意事项

1. **所有 DCC 操作都是异步的** — 提交后必须用 `maya_query_task` 轮询
2. **链式执行中不要自行打开文件** — 链引擎会统一打开 source_path
3. **单技能执行也会自动打开 source_path** — `execute_dcc_skill` 会在执行技能前自动打开文件（pipeline 类型除外）
4. **大型绑定文件打开慢** — rig 文件可能需要 60-120 秒
5. **exec_code 是万能后门** — 任何 cmds/OpenMaya 操作都可以通过它实现
6. **操作不可逆** — 清理权重等操作会修改场景，建议先备份或用 save_scene 另存
7. **save_scene 是链的终点站** — 所有破坏性链式操作的最后一步**必须**是 save_scene

## 安全守卫机制

DCC adapter 在每次技能执行后会检查当前场景路径：
- 如果场景指向受保护路径（如 X 盘），会**断开文件路径关联**（`bpy.data.filepath = ''` / `cmds.file(rename='')`）
- 场景数据保留在内存中，后续技能可以继续操作
- 这防止了意外保存回受保护路径，同时不影响只读操作（导出、采集信息等）

## NEEDS_ATTENTION 人机决策

当技能返回 `status: 'NEEDS_ATTENTION'` 时，**场景已 hold 在 Maya 内存中**，等待用户决策。

**你必须**：
1. 从 `suggested_actions` 中提取选项
2. **逐条列出** 给用户看，编号标注
3. **等待用户选择**，不要自行决定
4. 用户选择后，提交对应的 `skill_id` + `parameters`（Maya 场景仍在内存中）
5. 如果 `report_path` 存在，告知用户报告位置

**示例对话**：
```
AI: ⚠️ 保存目标在受保护的服务器路径，场景已 hold 在 Maya 中。
    请选择：
    [1] 另存为本地路径: .../sandbox/manual_save/hero_v003.ma
    [2] 我自己指定保存路径
    [3] 放弃保存（场景变更丢失）

用户: 1

AI: → 调用 save_scene(save_path='.../sandbox/manual_save/hero_v003.ma')
```

**严禁**：遇到 NEEDS_ATTENTION 时自动选择选项或跳过展示。

## 常见错误恢复

| 错误 | 原因 | 恢复 |
|---|---|---|
| `SUBMIT_FAILED` | Redis/Celery 未运行 | 提醒用户启动服务 |
| `NOT_FOUND` | 任务尚未开始或队列路由不匹配 | 等待 5 秒重试。持续 NOT_FOUND 则检查 Worker 是否在对应队列监听 |
| `TIMEOUT` | DCC 执行超时 | 检查文件是否损坏或操作是否过重 |
| `未找到 cache 组` | Blender 场景中没有名为 cache 的顶层 Empty | 用 exec_code 检查场景层级，确认组名 |
| `WinError 2` | Worker 启动找不到 celery | Celery 安装在 conda env `cgi_pipeline` 中，不在系统 PATH |

## 运维关键知识

### Worker 启动

Celery 安装在 **conda 环境 `cgi_pipeline`** 中，不是系统 Python：

```bash
# 正确启动方式
conda run -n cgi_pipeline python -m celery -A core.tasks worker -Q maya --pool=solo -c 1 -l info

# 错误方式（会 WinError 2）
celery -A tasks worker  # ← 系统PATH没有celery，模块名也错
```

### 队列路由

三种队列，按技能的 DCC 类型自动路由：

| DCC 类型 | 队列 | Worker |
|---|---|---|
| `maya` | `dcc_queue` | Maya Worker（mayapy 进程） |
| `blender` | `blender_queue` | Blender Worker（blender --background 进程） |
| `pipeline` | `dcc_queue` | 复用 Maya Worker 进程（PipelineWorker 在内部自行创建 DCC 子进程） |

MCP 会自动拉起对应 Worker。pipeline 类型（如 `export_abc_auto`）共用 maya 的 `dcc_queue`。

### exec_code 注意

`exec_code` / `blender_exec_code` 传入 `source_path` 后，Celery 会**自动打开该文件**再执行代码。代码中不需要手动 `cmds.file(open)` 或 `bpy.ops.wm.open_mainfile()`。

### 环境配置

| 项目 | 值 |
|---|---|
| 项目根目录 | `Y:\GGbommer\scripts\CGI_Pipeline` |
| conda 环境 | `cgi_pipeline` |
| Redis | 本地 `redis-server` 端口 6379 |
| Maya | `C:\Program Files\Autodesk\Maya2025` |
| Blender | 由 `.env` 中 `BLENDER_PATH` 指定 |
| .env | 所有路径配置集中在 `.env` 文件 |
