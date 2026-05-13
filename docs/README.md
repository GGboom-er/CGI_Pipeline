# CGI Pipeline 文档索引

本文档目录只保留当前可执行规范和稳定参考。历史方案、备份文件、临时代码片段已归档到 `archive/`，不得作为当前实现依据。

## 唯一入口

| 文档 | 用途 |
|---|---|
| `../skills/build_pipeline_skill/SKILL.md` | **唯一 skill 规范文档**。生成、更新、整理、审查 skill 只看这里。 |
| `architecture/pipeline_runtime_contract_v1.md` | 运行时总规范。只说明 AI、CLI、Dashboard、workflow、sandbox 的整体规则。 |
| `architecture/runtime_task_report.md` | `REPORT.md` 运行中报告机制。报告字段仍以 `../skills/build_pipeline_skill/SKILL.md` 的标准执行记录为准。 |
| `architecture/compare_and_assembly_pipeline_plan.md` | 资产对比、场景内 compare、ABC 拼装、材质赋予和 sync 的专项规范。 |
| `architecture/mesh_pairing_phased_logic.md` | 当前三步漏斗配对逻辑说明。 |
| `standards/` | 项目与 DCC 生产规范，例如 Maya QC 与 YSJ Maya 文件规范。 |

## Skill 拆解

`archive/skills/` 保存已落地节点的拆解说明和历史决策，不作为通用 skill 规范。具体 skill 的实时参数以 `skills/{skill_id}/SKILL.md` 为准；通用规则以 `../skills/build_pipeline_skill/SKILL.md` 为准。

## 参考资料

`reference/` 保存项目定义手册、图片和表格等非运行时契约资料。

## 归档资料

`archive/` 仅用于追溯：

- `archive/backups/`：历史 `.bak_*` 文件。
- `archive/code_samples/`：旧实验脚本和临时代码片段。
- `archive/history/`：已被当前规范替代的设计白皮书和历史方案。

归档资料不得直接指导当前代码修改；需要复用时，先与现行规范和代码核对。
