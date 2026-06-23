# AI 必读包说明

`docs/ai_startup/` 是所有 AI 会话的固定必读目录。根目录 `AGENTS.md` 负责强制指向这里；本目录负责承载需要持续整理的启动上下文。

必读顺序固定如下：

1. `AGENTS.md`
2. `docs/ai_startup/00_STARTUP_PROTOCOL.md`
3. `docs/ai_startup/01_PROJECT_FOUNDATION.md`
4. `docs/ai_startup/02_KNOWLEDGE_BASE.md`
5. `docs/ai_startup/03_DOC_INDEX.md`

条件必读不变：

- 涉及新增、修改、整理、审查 skill，或触及跨 skill 数据契约时，必须读 `skills/build_pipeline_skill/SKILL.md`。
- 涉及具体专题时，按 `03_DOC_INDEX.md` 打开对应专项文档。
- `tasks/lessons.md` 只用 `rg` 按关键词检索，不整篇作为启动上下文。

旧路径 `memory/README.md`、`docs/PROJECT_FOUNDATION.md`、`docs/KNOWLEDGE_BASE.md`、`docs/README.md` 不再保留。新增规则不要写回旧路径。
