# CGI Pipeline 文档索引

本文档目录只保留当前可执行规范和稳定参考。历史方案、备份文件、临时代码片段已归档到 `archive/`，不得作为当前实现依据。

## 权威规范

| 文档 | 用途 |
|---|---|
| `pipeline_runtime_contract_v1.md` | 运行时总规范。AI、CLI、Dashboard、自动任务共同遵守的唯一运行时契约。 |
| `skill_development_guide.md` | 新增 skill 的开发入口说明。硬契约以 `skills/CONVENTION.md` 为准。 |
| `runtime_task_report.md` | 唯一 `REPORT.md` 运行中报告机制。 |
| `compare_and_assembly_pipeline_plan.md` | 资产对比、场景内 compare、ABC 拼装、材质赋予和 sync 的专项规范。 |
| `mesh_pairing_phased_logic.md` | 当前三步漏斗配对逻辑说明。 |

## Skill 拆解

`docs/skills/` 保留已落地节点的职责边界、输入输出契约和验证点。具体 skill 的实时参数仍以 `skills/{skill_id}/SKILL.md` 为准。

## 参考资料

`reference/` 保存项目定义手册、图片和表格等非运行时契约资料。

## 归档资料

`archive/` 仅用于追溯：

- `archive/backups/`：历史 `.bak_*` 文件。
- `archive/code_samples/`：旧实验脚本和临时代码片段。
- `archive/history/`：已被当前规范替代的设计白皮书和历史方案。

归档资料不得直接指导当前代码修改；需要复用时，先与现行规范和代码核对。
