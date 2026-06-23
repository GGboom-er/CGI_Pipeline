# CGI Pipeline 文档索引

本文件是 `docs/ai_startup/` 固定必读包的第 4 份文件。本文档目录只保留当前可执行规范和稳定参考。历史方案、备份文件和运行产物不得作为当前实现依据。

每个任务开工前先读 `AGENTS.md` 和 `docs/ai_startup/` 固定必读包，然后按本文索引打开专项文档。上下文压缩、重新登录或新会话接手时也执行同一顺序。

## 唯一入口

| 文档 | 用途 |
|---|---|
| `docs/ai_startup/00_STARTUP_PROTOCOL.md` | **AI 启动协议**。所有会话启动、上下文恢复、问题闭环和经验归档归属先看这里。 |
| `docs/ai_startup/01_PROJECT_FOUNDATION.md` | **项目基石文档**。给新手和新接手 AI 的全局框架、八个视角、当前能力和边界。 |
| `docs/ai_startup/02_KNOWLEDGE_BASE.md` | **当前知识库入口**。记录主 workflow、skill/report 契约和近期验证结论。 |
| `docs/ai_startup/03_DOC_INDEX.md` | **文档索引**。本文件，按任务打开专项文档。 |
| `skills/build_pipeline_skill/SKILL.md` | **唯一 skill 规范文档**。生成、更新、整理、审查 skill 只看这里。 |
| `docs/architecture/pipeline_runtime_contract_v1.md` | 运行时总规范。只说明 AI、CLI、Dashboard、workflow、sandbox 的整体规则。 |
| `docs/architecture/runtime_task_report.md` | `REPORT.md` 运行中报告机制。报告字段仍以 `skills/build_pipeline_skill/SKILL.md` 的标准执行记录为准。 |
| `docs/architecture/compare_and_assembly_pipeline_plan.md` | 资产对比、场景内 compare、ABC 拼装、材质赋予和 sync 的专项规范。 |
| `docs/architecture/mesh_pairing_phased_logic.md` | 当前三步漏斗配对逻辑说明。 |
| `docs/architecture/deformation_inheritance_plan_and_validation.md` | 自动形变继承规范：方案边界、第一性原理、实现逻辑、关键决策和测试准则。详细实验迭代日志已拆分至 `docs/archive/history/deformation_inheritance_validation_log.md`。 |
| `docs/standards/` | 项目与 DCC 生产规范，例如 Maya QC 与 YSJ Maya 文件规范。 |

## 合并与更新规则

- 新增稳定规则前，先判断它属于知识库、运行时、skill、报告、对比拼装、mesh 配对、形变继承还是测试门禁。
- 新增跨 AI 启动、问题闭环或经验归档归属规则时，先更新 `docs/ai_startup/00_STARTUP_PROTOCOL.md`。
- 新增项目全局框架、目录职责、新手地图或能力边界规则时，先更新 `docs/ai_startup/01_PROJECT_FOUNDATION.md`。
- 不再为同一规则新建第二份 Markdown；直接更新对应权威文档，并在 `docs/ai_startup/02_KNOWLEDGE_BASE.md` 保留入口摘要。
- `tasks/todo.md` 与 `tasks/lessons.md` 只记录任务和避坑历史，不作为当前规范来源。
- 修改 workflow、skill `output`、报告字段或验证门禁时，必须同步更新本文索引、`docs/ai_startup/02_KNOWLEDGE_BASE.md` 和对应专项文档。

## Skill 拆解

`archive/skills/` 保存已落地节点的拆解说明和历史决策，不作为通用 skill 规范。具体 skill 的实时参数以 `skills/{skill_id}/SKILL.md` 为准；通用规则以 `../skills/build_pipeline_skill/SKILL.md` 为准。

## 参考资料

`reference/` 保存项目定义手册、图片和表格等非运行时契约资料。

## 归档资料

`archive/` 仅用于追溯：

- `archive/backups/`：历史 `.bak_*` 文件。
- `archive/history/`：已被当前规范替代的设计白皮书和历史方案。

归档资料不得直接指导当前代码修改；需要复用时，先与现行规范和代码核对。
