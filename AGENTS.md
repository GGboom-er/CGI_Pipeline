# >>> Notes Global Bridge <<<
# 自动追加 — 让本项目继承 Notes 治理层（红线/准则/触发条件/启动协议）
@Y:/GGbommer/scripts/Notes/AGENTS.md
# <<< Notes Global Bridge >>>

# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 仓库概览

CGI Pipeline v2.0 —— 一套自动化 DCC 资产管线。技能（skill）跑在后台的 Maya / Blender / 纯 Python worker 上，由 Celery 调度，FastMCP server 把每个技能包装成强类型 MCP tool 暴露出去。技能是 `skills/` 下的自包含文件夹；`core/skill_registry.py` 启动时扫 `SKILL.md` 自动生成 tool 元数据，**加新技能基本不需要动 registry 或 MCP 代码**，放好文件夹重启即可。

运行平台：**Windows 10+**、**Maya 2025**、**Blender 4.x**、conda 环境 `cgi_pipeline`（Python 3.11）。Shell 是 Windows 上的 bash，用 `/dev/null`、正斜杠。

## 开工必读协议

每个任务开工前必须主动读取 `docs/ai_startup/` 固定必读包，按文件名前缀顺序读完 `00_STARTUP_PROTOCOL.md`、`01_PROJECT_FOUNDATION.md`、`02_KNOWLEDGE_BASE.md`、`03_DOC_INDEX.md`，并按其中的“后续开发基线”打开相关专项文档。不要等用户提醒再读，也不要只凭聊天上下文、历史记忆或上一次任务结论判断当前规则。

上下文压缩、重新登录或新会话接手时，先读取本文件，再读取 `docs/ai_startup/` 固定必读包。涉及具体问题时，用 `rg` 在 `tasks/lessons.md` 查关键词，避免把整份历史流水账当当前规范。

开工读文档的目标是先理解用户需求场景：项目全局框架、当前主 workflow、skill 输出契约、报告格式、测试门禁和已知未完成专题都以知识库链接到的权威文档为准。

规则更新必须落地到知识库：

- 会话启动、问题闭环、经验归档归属规则写 `docs/ai_startup/00_STARTUP_PROTOCOL.md`。
- 项目全局框架、新手地图、能力边界和目录职责写 `docs/ai_startup/01_PROJECT_FOUNDATION.md`。
- 当前稳定事实写入 `docs/ai_startup/02_KNOWLEDGE_BASE.md` 或其链接的权威专项文档。
- 文档地图、专项入口和文档合并规则写 `docs/ai_startup/03_DOC_INDEX.md`。
- 新增/改造 skill 的规则只写 `skills/build_pipeline_skill/SKILL.md`。
- 任务闭环和待办写 `tasks/todo.md`。
- 已踩坑经验写 `tasks/lessons.md`，格式保持 `[触发条件] → [根因] → [正确方案] → [避坑规则]`。
- `docs/archive/`、`backups/`、`projects/` 和 `.info/` 只作追溯或运行产物，不作为当前实现依据。

## 常用命令

### 环境与服务

```powershell
# 一次性部署：创建 conda 环境 cgi_pipeline，装依赖，生成 .env
bin\setup.bat

conda activate cgi_pipeline

# Dashboard 会自动拉起 Redis 和 Maya/Blender worker
python -m dashboard.app

# 手动起 worker（调试用）
python -m celery -A core.tasks worker -Q dcc_queue     --pool=solo -c 1 -l info --hostname=cgi_maya@%h      # Maya + pipeline
python -m celery -A core.tasks worker -Q blender_queue --pool=solo -c 1 -l info --hostname=cgi_blender@%h
python -m celery -A core.tasks worker -Q workflow_queue --pool=solo -c 1 -l info --hostname=cgi_workflow@%h
```

**Celery 装在 conda 环境里，不在系统 PATH**。没激活环境就用 `conda run -n cgi_pipeline ...`。Maya worker 必须用 `mayapy` 启动，这块 `service_manager.py` 会自动处理。
`service_manager.py` 会同时检查 PID 文件和 Celery 队列心跳；心跳窗口默认 5 秒，PID 存活但对应队列心跳丢失时会自动重启 Worker。

### CLI（脱离 AI 直接跑技能/工作流）

```bash
python cli.py list-skills [-v]
python cli.py list-workflows
python cli.py resolve-asset --project ysj --asset xiaotianquan
python cli.py run-skill <skill_id> --project ysj --asset <name> --source-path <file> [--param k=v ...]
python cli.py run-chain --steps s1,s2,s3 --source-path <file> [--param k=v ...]
```

### 测试

测试是普通脚本，不用 pytest，各自单独跑：

```bash
python tests/test_rig_sync_profile.py   # P0 单元测试，不依赖 DCC
python tests/test_pipeline_compare.py
python tests/test_compare_result_contract.py
python tests/test_sync_contract.py
python tests/test_sync_action_dispatch.py
python tests/test_abc_reader.py
```

模式：每个测试文件用本地 `_check(name, condition)` 辅助函数，打印 `[PASS]/[FAIL]`。**没有统一的 test runner**，按改动相关性挑着跑。需要 DCC 的测试在 `tests/e2e_abc/` 下或走 `mayapy` 子进程（如 `run_sync_mayapy.py`）。

## 架构

### 执行分层

```
AI (Codex/MCP) ──► mcp_server/ (FastMCP)
                         │
                         ▼
                  core/tasks.py (Celery)
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
       dcc_queue    blender_queue   (pipeline → dcc_queue)
       Maya Worker  Blender Worker  PipelineWorker（自己拉 DCC 子进程）
       (mayapy)     (--background)
            │            │
            ▼            ▼
        skills/{id}/{id}.py::execute(payload)
```

三种运行模式共享同一个 Celery 后端：

| 模式 | 入口 | 行为 |
|---|---|---|
| 单技能 | `execute_skill` | 单次 DCC 调用，自动打开 `source_path`（`dcc=pipeline` 类除外） |
| 链式 | `execute_chain` | 同一 DCC 会话多步串行。破坏性链的最后一步**必须**是 `save_scene` |
| 工作流 | `execute_workflow` | 按每个技能的 `dcc` 自动分段，各段派到对应 worker 执行；段间通过 `{{outputs.step_id.field}}` 模板传数据（Redis 持久化，支持断点恢复） |

### 技能契约

每个技能是 `skills/{skill_id}/{skill_id}.py`，暴露 `execute(payload) -> dict`。**返回值必须用 `core.receipt.make_receipt(...)` 构造** —— 其他格式一律不算合法回执（dashboard、审计账本、workflow 段间传递都依赖这个约定）。完整构建规则见 `skills/build_pipeline_skill/SKILL.md`。

```python
def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})
    # ...
    return make_receipt(
        skill_id='xxx',
        status='SUCCESS',
        start_time=t0,
        input={'source_path': payload.get('source_path'), **params},
        output={'output_path': ..., 'mesh_count': ...},
    )
```

状态码：`SUCCESS / ERROR / BLOCKED / TIMEOUT / CHAIN_ABORTED / AUDIT_FAILED / CHAIN_AUDIT_FAILED / WORKFLOW_AUDIT_FAILED`。后台 pipeline 不等待人工决策；质检或契约不通过时返回 `AUDIT_FAILED` 并生成报告。

标准执行记录、`input/output`、报告 Details、旧字段兼容边界等 skill 规则只维护在 `skills/build_pipeline_skill/SKILL.md`。本文件只保留运行时边界：新增或改造 skill 必须按该文件设计，workflow 只读取上游 `output`，报告不读取 Markdown 作为机器数据。

### 节点化参数命名规范（SOP）

技能是 SOP 图里的节点；参数名是输入槽。所以参数名必须**自文档化角色**，不能是匿名占位符。

- **对比类 / 双入口节点**：用 `_source` / `_target` 后缀。
  - `input_source` / `input_target`（路径）、`label_source` / `label_target`（标签）。
  - 旧代码里的 `input_a / input_b / label_a / label_b` 保持向后兼容接受，但新 SKILL.md 与 workflow 一律用新名。
- **投射/同步类节点**：source 侧多种数据形态用 `source_<type>`。
  - `source_abc`（含完整拓扑，推荐）、`source_info`（降级，仅指纹）。
  - 老名 `abc_path / tex_json` 只在过渡期接受。
- **框架层隐式键**：`payload.source_path` 是 Celery 约定的"本次 skill 要打开的 DCC 场景"。**它不一定是算法语义上的 source**——例如 `maya_sync_rig_incremental` 里 `source_path` 承载的是 target rig 场景。这种语义冲突必须在 SKILL.md 的 🔴 核心限制里明示。
- **算法层内部变量（`core/`）**：不强制改 `a/b` 为 `source/target`。算法原语面对的是数学意义上两个集合，不一定有角色。但注释里要写清当前调用约定。

### 技能注册与 MCP 自动生成

- `core/skill_registry.py` 启动时扫所有 `skills/*/SKILL.md`，解析 YAML frontmatter，构建注册表。
- `mcp_server/server.py` 读注册表，为每个技能**自动生成** Pydantic 输入模型和 MCP tool —— 不需要手动挂 MCP tool。
- frontmatter 里的 `dcc` 决定队列路由：`maya` → `dcc_queue`，`blender` → `blender_queue`，`pipeline` → `dcc_queue`（由 PipelineWorker 执行，必要时自己起 DCC 子进程）。

### 核心算法层（`core/`）

`core/` 刻意**不依赖 DCC** —— 所有函数吃 dict、吐 dict，不落盘 JSON。这样在 Maya/Blender 进程里跑的技能可以直接调，省掉"落 JSON → 读 JSON"的来回。

关键模块：

| 模块 | 职责 |
|---|---|
| `asset_info_schema.py` | `compare(a_dict, b_dict, profile)` —— 三步漏斗（路径匹配 → 同点数池 → 空间分析）。算法层保留 7 种 `actionability` 标签（IDENTICAL/ORIG_INJECT/MODIFIED/MERGE/SPLIT/NEW/DELETE），标准记录层聚合为 4 类（matched_same / matched_different / only_source / only_target）。**两层共存，不是新旧替换**——MERGE/SPLIT 的拓扑关系对下游权重传递必需。**输入是 dict 对 dict，JSON 是可选的。** |
| `abc_reader.py` | `read_abc_as_info(abc_path)` —— PyAlembic → 同一份 asset_info dict |
| `compare_result_io.py` | compare_result.v1 写出、读取 `_info.json`/ABC、`.info` 输出路径推导；机器 JSON 可保留内部 `paired/only_a/only_b/actionability` |
| `pairing_report.py` | 将 compare_result 聚合为 source 侧 4 种事实去向，供标准执行记录和审计使用 |
| `spatial_transfer.py` / `deformation_field.py` / `laplacian_diffuse.py` / `biharmonic_diffuse.py` / `non_rigid_registration.py` / `unified_deformation_field.py` | 权重/BlendShape 投射的底层算法，`maya_sync_rig_incremental` 调用 |
| `config_loader.py` | 配置金字塔：`pipeline_manifest.json` → `{project}_config.json` → 技能默认值 → `.env` |
| `path_guard.py` | 拦截到 `readonly_drives`（默认 `X:`）和 manifest 声明的 UNC 前缀的写入 |
| `receipt.py` | 唯一合法的标准执行记录构造器 |
| `run_archive.py` | 按任务隔离的沙盒（`runs/`），带 manifest 持久化 |
| `service_manager.py` | Dashboard 启动时自动拉起 Redis 和对应 worker |

### DCC 共享采集层

Maya 场景内几何采集放在 `dccs/maya/asset_info_collector.py`，不要放进 `core/`。`maya_build_asset_info`、`maya_compare_asset_in_scene` 和 `maya_sync_rig_incremental` 必须复用同一套采集逻辑：

- 从 `cache_group` 全层级子孙里自身带 mesh shape 的 transform 出发。
- mesh key 使用标准非 intermediate mesh shape 的绝对 DAG 路径；transform 可见性不参与过滤。
- 顶点数据优先来自 Maya 图关系求出的唯一 Orig，不靠名称猜测；兜底才接受同 transform 下唯一有效的 intermediate mesh。
- 找不到唯一 Orig 时保留 mesh 条目但写空几何；诊断由对比/报告暴露，不由采集器修复。

### 对比/拼装主线

推荐工作流不再“导出 Maya JSON 再开 Maya 拼装”。主线是：

```text
resolve_asset_files
-> blender_export_abc
-> blender_extract_materials
-> maya_check_asset_hierarchy
-> maya_fix_asset_hierarchy
-> maya_compare_asset_in_scene
-> maya_sync_rig_incremental
-> maya_check_asset_hierarchy
-> maya_fix_shape_names
-> maya_apply_materials
-> maya_compare_asset_in_scene
-> save_scene
```

`pipeline_compare_asset` 保留为纯数据入口：只有当 source/target 都已经是 `_info.json` 或 ABC 时使用。`maya_sync_rig_incremental` 是执行器，必须消费前置 `compare_result`，不再独立重算对比。主 workflow 的文件入口必须节点化：路径查找属于 `resolve_asset_files`，层级修复属于 `maya_check_asset_hierarchy` / `maya_fix_asset_hierarchy`，文件隔离属于调度层，DCC skill 只消费解析后的路径。

### 配置金字塔（四层）

每层覆盖上一层：

1. `config/pipeline_manifest.json` —— 系统宪法：只读盘、状态码、链规则、队列映射，**唯一真相源**
2. `config/{project}_config.json` —— 项目级路径/资产/阶段规则。**新项目 = 拷一份这个文件，零代码改动**
3. 技能 `SKILL.md` frontmatter 里的默认值
4. `.env` —— 本机覆盖（路径、二进制位置）

### 材质赋予的两条路径（是设计，不是重复）

两个技能对应两种不同需求 —— **不要合并**：

- `maya_assign_udim_materials` —— 没有来源信息时用。读 Maya cache mesh 的 UV 象限（UDIM tile），按贴图目录命名规范反推材质
- `maya_apply_materials` —— 有 `_materials.json`（Blender 侧 `blender_extract_materials` 生成）时用，按面显式赋予

`maya_sync_rig_incremental` 内部的材质步是调这两个，不是第三套实现。

### 资产路径规范

资产发布源走 `{root}/{project}/pub/assets/{type}/{asset}/{dept}/{task}/`，从 `pub` 读且视为受保护。AI 处理产物写任务沙盒 `projects/{project}/{YYYYMMDD_HHMMSS}_{asset}/`；正式发布另走独立 publish 节点。路径解析交给 `core/asset_resolver.py` + 项目配置。**不要硬编码路径。**

## 红线（运行时强制）

运行时强制规则：

1. **不许吞异常**（`except: pass`）。要通过 `make_receipt(status='ERROR', error=...)` 翻译上报。未捕获的 DCC 异常会让 worker 卡死。
2. **不许写受保护根目录**（默认 `X:`）。`path_guard` 会拦截，`cmds.file(save=...)` / `bpy.ops.wm.save_as_mainfile()` 指向保护路径会被 block。写入必须落 `runs/` 或沙盒。
3. **不许加硬超时**。几 GB 的 ABC、千万面的 rig 是常态。用 `internals._submit_to_celery` 拿 `task_id` 无限期轮询。
4. **标准执行记录不做展示层截断**；超大机器数据应落 JSON/ABC 文件，并在 `output.output_path` 暴露路径。
5. **`save_scene` 是破坏性链的最后一步**，不能放中间。
6. **DCC 操作全是异步的** —— `execute_skill/chain/workflow` 之后必须用 `query_task_status` 轮询（每 3-5 秒一次）。

## 在本仓库里工作的约定

- "看/改当前场景"类请求必须走 `cgi-pipeline` MCP 的 `maya_exec_code` 或具名 `maya_` Tool，参数必须包含 `execution_mode: "foreground"` 和用户指定的 `foreground_port`。background 模式只用于批处理。
- 禁止使用旧 `maya-live`、默认 commandPort 或省略 `foreground_port` 去连 Maya。多 Maya 会话同时存在时，省略端口会被 MCP 返回 `NEEDS_ATTENTION` 拦截；端口未知时先调用 `maya_list_foreground_sessions` 或询问用户。
- 通过原始 Python MCP Client 手动 `call_tool` 时，FastMCP 入参需要外层 `{"params": {...}}`；不要把 `code/execution_mode/foreground_port` 平铺到顶层。
- Maya 端口推荐开启命令：`cmds.commandPort(name=":7009", sourceType="python", echoOutput=True)`；调试/长任务必须保留 `echoOutput=True`，不要关输出。
- 用户说"写一个新技能"或要求整理/修改 skill 规范时**不要直接写代码**。打开 `skills/build_pipeline_skill/SKILL.md` 走唯一构建规则（意图捕获 → 蓝图 → 生成/修改 → 契约检查）。
- 技能代码放在各自文件夹里（`skills/{id}/{id}.py`），不能放 `skills/` 根目录。`__init__.py` 负责 re-export `execute`。
- `exec_code` / `blender_exec_code` 传了 `source_path` 时，Celery 会**自动打开文件**再跑代码 —— 别在代码片段里再调 `cmds.file(open=...)`。
- 链式执行已经打开了初始 `source_path`，第一步别再打开一次。
- 跨 DCC workflow 的段间数据走标准执行记录 `output` 字段，在 `workflows/*.json` 里用 `{{outputs.step_id.output_path}}` 或 `{{outputs.step_id.result.rig_path}}` 表达；JSON/ABC/materials/compare_result 等机器中间产物统一写任务沙盒 `.info`。

## 第三方及研究资料隔离 (Vendor & Research Exclusion)

- 本仓库中的 `research/` 和 `vendor/` 目录包含大量第三方库或前沿算法原生资料。
- 全局搜索、规范重构或文档清理时默认跳过这些目录，避免破坏外部依赖库的原始结构。
