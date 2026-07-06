# CGI Pipeline 项目基石文档

更新时间：2026-05-14

本文是 `docs/ai_startup/` 固定必读包的第 2 份文件，也是给新手和新接手 AI 的项目说明。它回答三个问题：这个仓库是什么框架、现在主要在干什么、它目前能做什么。本文是配置化必读文档，必须由 `AGENTS.md` 强制引用，不依赖聊天记忆。

阅读定位：

- 本文负责建立全局地图，不替代专项规范。
- 当前稳定事实和近期验证结论看 `docs/ai_startup/02_KNOWLEDGE_BASE.md`。
- 运行时边界看 `docs/architecture/pipeline_runtime_contract_v1.md`。
- 新增或改造 skill 看 `skills/build_pipeline_skill/SKILL.md`。
- 任务历史和踩坑索引用 `rg` 查 `tasks/lessons.md`，不要把历史流水账当当前规范。

## 一、产品定位视角

CGI Pipeline 是一套自动化 DCC 管线系统。DCC 指 Digital Content Creation，当前主要覆盖 Maya、Blender，并保留 UE 适配骨架。

它的核心目标不是写一个一次性脚本，而是把资产处理动作沉淀成可复用节点：

- AI 可以通过 MCP 调用。
- 用户可以通过 CLI 调用。
- Dashboard 可以触发和观察任务。
- Workflow 可以脱离 AI 批处理运行。

当前主线服务的是资产从 Blender/贴图阶段到 Maya 绑定场景的验证、拼装、材质赋予、层级修复、对比和保存。典型任务是：只给项目名和资产名，系统自动定位服务器源文件，进入沙盒，导出 ABC，采集材质，打开或构建 Maya 场景，对比 source 与 target，执行同步，输出报告和最终 `.ma`。

边界也要明确：

- `publish_asset` 不属于当前主处理链路，正式发布应是独立节点。
- Live BS / 动态 BlendShape 仍是未完成专题，不能当作已经完全并入主 workflow。
- `research/`、`vendor/`、`docs/archive/`、`backups/`、`projects/`、`runs/` 是参考、第三方、历史或运行产物，不能作为当前实现规范。

## 二、工作区结构视角

当前仓库可以按职责分成以下层：

| 目录或文件 | 当前职责 |
|---|---|
| `AGENTS.md` | 仓库级 AI 运行规范和开工必读协议。 |
| `docs/ai_startup/00_STARTUP_PROTOCOL.md` | 所有 AI 会话启动、问题闭环、经验归档的第一读取入口。 |
| `docs/ai_startup/01_PROJECT_FOUNDATION.md` | 本文，面向新手的项目全局地图和八个视角基石。 |
| `docs/ai_startup/02_KNOWLEDGE_BASE.md` | 当前稳定事实、主 workflow、近期真实资产验证结论。 |
| `docs/architecture/` | 运行时、报告、对比拼装、mesh 配对、形变继承等专项权威文档。 |
| `config/` | 系统宪法、项目路径规则、报告标签等配置中心。 |
| `core/` | 调度、注册、对比算法、ABC 读取、沙盒、报告、服务管理等核心层。 |
| `dccs/` | Maya、Blender、UE worker 和 DCC 适配层。 |
| `skills/` | 可被调度的技能节点，每个技能一个文件夹。 |
| `workflows/` | 跨 skill、跨 DCC 的 JSON 工作流定义。 |
| `mcp_server/` | FastMCP 服务层，把技能和操作暴露给 AI/MCP 客户端。 |
| `dashboard/` | Web 面板、任务观察、服务状态和图式操作入口。 |
| `cli.py` | 命令行入口，用于列技能、列 workflow、解析资产、跑 skill/chain/workflow。 |
| `tests/` | 轻量契约测试和算法测试，没有统一 test runner。 |
| `tasks/` | 任务清单和踩坑索引，不是当前规范来源。 |
| `app/` | 较大的历史/手工 DCC 工具层，包含多 DCC 和 ShotGrid 相关工具；可参考，但不是当前 Celery-skill 主线。 |
| `tools/` | 算法实验、辅助脚本和测试资产，复用前必须按当前契约审查。 |

运行时目录包括 `audit/`、`logs/`、`runtime/`、`projects/`、`runs/`、`ipc/`。这些目录承载执行结果、审计和临时状态，默认不作为规范来源。

## 三、运行架构视角

系统主执行链路如下：

```text
AI / CLI / Dashboard
  -> MCP Server 或 cli.py
  -> Celery 任务
  -> Redis 状态与队列
  -> Maya / Blender / Pipeline Worker
  -> skills/{skill_id}/execute(payload)
  -> 标准执行记录 receipt
  -> audit / REPORT.md / manifest.json / .info
```

关键运行事实：

- Maya skill 路由到 `dcc_queue`。
- Blender skill 路由到 `blender_queue`。
- Workflow 路由到 `workflow_queue`。
- Pipeline 类 skill 复用 `dcc_queue`，但可在内部自行拉起 DCC 子进程或做纯 Python 处理。
- Dashboard 启动时会通过 `core/service_manager.py` 管理 Redis 和 worker。
- 服务健康不是只看 PID，还要看 Celery `active_queues` 心跳。
- DCC 任务是异步任务，提交后必须轮询终态。

Maya 当前前台场景的操作有单独边界：必须用 foreground 模式并显式传 `foreground_port`，不能省略端口或依赖旧 commandPort 默认行为。

## 四、Skill 节点视角

Skill 是这个系统的最小可复用能力单元。每个 skill 位于：

```text
skills/{skill_id}/
  SKILL.md
  {skill_id}.py
  __init__.py
```

注册方式：

- `core/skill_registry.py` 启动时扫描 `skills/*/SKILL.md`。
- `SKILL.md` frontmatter 中的 `dcc` 决定路由。
- `mcp_server/models.py` 和 `mcp_server/tools_operations.py` 会根据注册表动态生成 MCP tool。

执行契约：

- 每个 skill 暴露 `execute(payload) -> dict`。
- 返回值必须用 `core.receipt.make_receipt(...)` 构造。
- `input` 只记录实际生效参数，用于审计和排障。
- `output` 是 workflow 下游连接和报告 `Details` 的唯一事实来源。
- 大型机器数据写 `.info` 中的 JSON/ABC/NPZ，`output` 只放路径、计数和短明细。
- 新 skill 不设计 `report_sections`、`summary/items/report_content` 这类旧展示字段。

当前 skill 规模按注册结果约为 45 个：Maya 约 34 个、Blender 约 5 个、Pipeline 约 6 个。能力覆盖打开/保存场景、导出 ABC、构建 mesh、采集 asset_info、检查 UV、修复层级、对比资产、同步绑定、赋材质、清理、报告生成等。

## 五、Workflow 编排视角

Workflow 是由多个 skill 组成的 JSON 图。它解决的问题是：跨 DCC、多步骤任务不能靠 AI 临时串命令，必须能稳定复跑。

Workflow 基本规则：

- `step_id` 是 workflow 内部引用名。
- `skill_id` 必须对应已注册 skill。
- `parameters` 只传 skill 声明过的参数。
- 下游通过 `{{outputs.step_id.field}}` 读取上游 receipt 的 `output.field`。
- 中间产物路径使用 `{{input.info_dir}}`。
- DCC 分段之间由调度层传递数据，不靠 Markdown 报告。
- 破坏性链路的最后一步必须是 `save_scene`。

当前共有 8 条 workflow：

| workflow_id | 主要用途 |
|---|---|
| `tex_to_rig_verify_and_sync` | 当前主线：解析 tex/rig，Blender 导 ABC/材质，Maya 对比、同步、后验、保存。 |
| `tex_to_rig_verify` | 只做 tex 到 rig 的解析、导 ABC、Maya 场景内对比并写 compare_result。 |
| `blender_to_maya_full_build` | 从 Blender 源文件导出 ABC，在空 Maya 场景中构建 mesh、赋材质、保存。 |
| `blender_tex_export` | Blender 导出 ABC、材质和 asset_info。 |
| `abc_import_with_materials` | Maya 侧从 ABC 构建 mesh 并赋材质。 |
| `full_cleanup_and_save` | Maya 权重清理、全清理、Shape 修复、法线处理、保存。 |
| `qc_and_publish` | QC 检查和发布门禁骨架。 |

当前主 workflow 是：

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

另外，`blender_to_maya_full_build` 已用于 `xycrowdbig`：只传资产名时按 `source_resolution` 定位 `uv/uvMaster` `.blend`，Maya 从空场景构建，输出层级为 `|Group|cache`，不生成错误的 `|ABC` 顶层。

## 六、数据契约视角

系统的核心数据不应该藏在 DCC 场景里，也不应该藏在 Markdown 里，而是以机器可读文件和标准记录流动。

主要数据类型：

| 数据 | 生产者 | 消费者 | 用途 |
|---|---|---|---|
| `asset_info` | Blender/Maya 采集器、ABC reader | compare/sync/report | 描述 mesh 拓扑、材质、UV、路径、指纹。 |
| `ABC` | Blender/Maya 导出、自动导出 | Maya 构建、compare、sync | source 的完整拓扑与 UV 主数据。 |
| `_materials.json` | `blender_extract_materials` | `maya_apply_materials` | per-face 材质、颜色和真实 color 贴图。 |
| `compare_result` | `maya_compare_asset_in_scene`、`pipeline_compare_asset` | `maya_sync_rig_incremental`、报告、巡航 | source/target 配对、差异、算法标签和审计。 |
| `REPORT.md` | 调度层报告 writer | 用户 | 用户可读结果，不作为下游机器输入。 |

当前重要规则：

- ABC archive 顶层 `ABC` 是文件容器，不是业务 DAG，构建 Maya 层级时必须剥离。
- 材质贴图只把真实 color/albedo/diffuse 接到 color；AO、normal、roughness、height、mask 等不得强行当 color 贴图。
- Maya asset_info 采集从标准非 intermediate mesh shape 出发，Orig 优先通过图关系查找，不能只靠名称猜测。
- compare 的用户视角聚合为 `matched_same`、`matched_different`、`only_source`、`only_target`；算法层仍保留 `IDENTICAL`、`ORIG_INJECT`、`MODIFIED`、`MERGE`、`SPLIT`、`NEW`、`DELETE` 等标签。

## 七、配置与安全视角

配置分四层：

1. `config/pipeline_manifest.json`：系统宪法，定义只读盘、状态码、队列、链规则、payload/receipt 边界。
2. `config/{project}_config.json`：项目路径、资产阶段、几何根、rig_sync_profile。
3. `skills/{skill_id}/SKILL.md`：技能级默认值和参数定义。
4. `.env`：本机路径覆盖，例如 Maya、Blender、Redis、项目根。

安全边界：

- `X:` 和服务器源资产视为只读。
- AI 处理产物写入任务沙盒 `projects/{project}/{YYYYMMDD_HHMMSS}_{asset}`。
- 机器中间产物统一写 `{sandbox}/.info/`。
- 最终用户关注沙盒根目录中的 `REPORT.md`、`manifest.json` 和最终 `.ma`。
- `path_guard` 拦截写受保护路径。
- DCC adapter 在执行后检查场景是否仍指向受保护路径，必要时断开文件路径关联。
- workflow 分段中只有首段默认继承全局 `source_path`；后续 DCC 段必须显式声明 `source_path`，否则从空场景开始。
- Warm Pool worker 收到空 `source_path` 必须显式新建空场景，不能沿用上一次任务的 DCC 内存状态。

## 八、入口、验证与沉淀视角

使用入口：

- CLI：`python cli.py list-skills`、`python cli.py list-workflows`、`python cli.py resolve-asset`、`python cli.py run-skill`、`python cli.py run-chain`、`python cli.py run-workflow`。
- MCP：AI 通过 `cgi_pipeline_mcp` 调用动态 skill、执行 workflow、查任务状态、查服务健康。
- Dashboard：`python -m dashboard.app` 后可查看任务、服务状态、报告和图式操作。
- Foreground Maya：只用于用户已经打开的 Maya 场景，必须显式传 `foreground_port`。

验证入口：

- `tests/README.md` 是当前轻量门禁清单。
- 本仓库没有统一 test runner，按改动范围单独跑测试脚本。
- 文档/知识库/启动链路变更至少跑 `python tests/test_docs_knowledge_contract.py`。
- workflow 输入、报告、层级、compare/sync、ABC reader、service manager 等有各自契约测试。
- 涉及真实 Maya/Blender 行为时，静态测试不够，需要补 DCC skill 或真实资产 workflow 巡航。

经验沉淀：

- 每次遇到问题并解决后，必须写入正确权威文档。
- 跨 skill 的经验写 `skills/build_pipeline_skill/SKILL.md`，不能散落到单个 skill 文档。
- 当前稳定事实写 `docs/ai_startup/02_KNOWLEDGE_BASE.md` 或对应专项文档。
- 启动读取、问题闭环、归档归属规则写 `docs/ai_startup/00_STARTUP_PROTOCOL.md`。
- 同时在 `tasks/lessons.md` 加一条短索引，格式为 `[触发条件] → [根因] → [正确方案] → [避坑规则]`。

## 新手最短上手路径

1. 读 `AGENTS.md` 和 `docs/ai_startup/` 固定必读包。
2. 用 `python cli.py list-workflows` 看当前有哪些 workflow。
3. 用 `python cli.py list-skills` 看当前可调用技能。
4. 先理解 `tex_to_rig_verify_and_sync` 主链路，再看 `blender_to_maya_full_build` 这种空 Maya 构建链路。
5. 做代码或文档改动前，先查相关专项文档和 `tasks/lessons.md` 关键词。
6. 改完后按 `tests/README.md` 跑对应门禁，并把新经验沉淀到正确位置。

## 当前能力总结

当前系统已经具备：

- 按项目配置解析资产 tex/rig/uv/mod 等阶段文件。
- 从 Blender 导出 ABC、FaceSet 和材质信息。
- 从 ABC 纯数据构建 Maya mesh，并保留层级和 UV。
- 检查并修复 Maya 资产层级。
- 采集 Maya/Blender/ABC asset_info。
- 做 source/target 对比，输出 compare_result。
- 基于 compare_result 执行增量 rig sync。
- 按材质 JSON 给 Maya mesh 按面赋材质。
- 检查贴图、UV set、Shape/Orig 命名、法线和发布前 QC。
- 生成统一 `REPORT.md` 和任务 manifest。
- 通过 CLI、MCP、Dashboard 三种入口运行同一套能力。

当前仍要谨慎对待：

- `publish_asset` 和正式发布链路不要混入当前处理 workflow。
- Live BS / 动态 BlendShape 仍需产品化验证。
- `app/`、`tools/`、`.agents/` 中可能有历史方案或实验脚本，引用前必须和本文、知识库、运行时契约及当前代码核对。
