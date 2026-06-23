# AI 启动必读协议

本文件是 `docs/ai_startup/` 固定必读包的第 1 份文件。无论是新会话、上下文压缩后恢复、换模型接手，还是继续旧任务，都必须按本文件顺序读取同目录必读包。

根目录 `AGENTS.md` 是仓库级配置入口；它负责强制指向本目录。真正需要持续整理和维护的启动上下文集中在 `docs/ai_startup/`。

## 启动读取顺序

1. `AGENTS.md`：仓库级配置入口，确认必须读取本目录。
2. `docs/ai_startup/00_STARTUP_PROTOCOL.md`：启动、闭环和知识归档规则。
3. `docs/ai_startup/01_PROJECT_FOUNDATION.md`：项目全局框架、八个视角、当前能力和边界。
4. `docs/ai_startup/02_KNOWLEDGE_BASE.md`：当前最小事实集、主 workflow、近期验证结论。
5. `docs/ai_startup/03_DOC_INDEX.md`：文档地图和专项文档入口。
6. `skills/build_pipeline_skill/SKILL.md`：只要涉及新增、修改、整理、审查 skill，或触及跨 skill 数据契约，必须读取。
7. `tasks/lessons.md`：只用 `rg` 按关键词检索历史坑点；它是索引，不是当前规范来源。

## 问题闭环协议

每次遇到问题并解决后，必须把经验沉淀成可复用知识，避免下个 AI 再犯同样错误。

闭环顺序：

1. 复现或确认问题现象：记录具体资产、workflow、skill、输入输出和失败点。
2. 定位根因：说明错误发生在数据生产者、调度层、DCC 操作层、报告层还是文档层。
3. 代码级修复：修在根因所在层，不用下游补丁掩盖上游错误。
4. 真实验证：按风险选择单元测试、契约测试、DCC skill 或真实 workflow。
5. 知识归档：把规则写入正确权威入口，并在 `tasks/lessons.md` 追加短索引。

## 知识归档归属

规则必须写在能被后续 AI 启动时读到的位置，不能只留在聊天记录、报告或单个任务产物里。

| 经验类型 | 写入位置 |
|---|---|
| 所有 AI 会话都必须遵守的启动、闭环、归档规则 | `docs/ai_startup/00_STARTUP_PROTOCOL.md` |
| 项目全局框架、新手地图、能力边界、目录职责 | `docs/ai_startup/01_PROJECT_FOUNDATION.md` |
| 当前稳定事实、主 workflow、近期真实资产验证结论 | `docs/ai_startup/02_KNOWLEDGE_BASE.md` |
| 文档地图、专项文档入口、文档合并规则 | `docs/ai_startup/03_DOC_INDEX.md` |
| 新增/改造 skill、标准执行记录、跨 skill 数据契约、通用 DCC 避坑 | `skills/build_pipeline_skill/SKILL.md` |
| AI/CLI/Dashboard/workflow/sandbox/worker 调度边界 | `docs/architecture/pipeline_runtime_contract_v1.md` |
| REPORT.md 展示和报告字段规则 | `docs/architecture/runtime_task_report.md` |
| 对比、拼装、层级修复、材质赋予、主 workflow 规则 | `docs/architecture/compare_and_assembly_pipeline_plan.md` |
| mesh 配对算法和算法标签 | `docs/architecture/mesh_pairing_phased_logic.md` |
| Skin/BS/Live BS 等形变继承专题 | `docs/architecture/deformation_inheritance_plan_and_validation.md` |
| 任务流水账和完成记录 | `tasks/todo.md` |
| 历史坑点短索引 | `tasks/lessons.md` |

## 禁止事项

- 不允许只在单个 `skills/{skill_id}/SKILL.md` 里记录跨 skill 经验。
- 不允许把 `tasks/lessons.md`、`tasks/todo.md`、`REPORT.md` 或聊天记录当作唯一规范来源。
- 不允许新增第二套启动必读入口；需要新增启动规则时先判断归属，再更新 `docs/ai_startup/` 内对应文件。
- 不允许解决完问题却不沉淀知识。若问题导致代码修复，必须同时留下可检索的经验或说明为什么它只是一次性数据问题。

## 交付前检查

交付前确认：

- 已按本文件读取启动上下文。
- 已把新经验写入正确权威入口。
- 已在 `tasks/lessons.md` 添加 `[触发条件] → [根因] → [正确方案] → [避坑规则]` 短索引。
- 已运行相关测试或说明无法验证的原因。
