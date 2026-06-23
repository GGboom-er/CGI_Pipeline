# CGI Pipeline 运行时总规范 v1

更新时间: 2026-05-14

本文档只定义 CGI Pipeline 的运行时边界：AI、CLI、Dashboard、workflow、sandbox、worker 和报告如何协同。上下文恢复入口看 `docs/ai_startup/02_KNOWLEDGE_BASE.md`；skill 生成、更新和输出规则只看 `skills/build_pipeline_skill/SKILL.md`；`REPORT.md` 渲染细节看 `runtime_task_report.md`。

## 1. 核心目标

CGI Pipeline 的目标是让同一套任务能稳定脱离 AI 运行。

系统必须同时支持：

- AI 通过 MCP 调用
- 用户通过 CLI 调用
- Dashboard 触发任务
- 自动巡航或批处理任务触发

同一个任务在不同入口下应使用同一套 skill、workflow、receipt、audit、report 和 sandbox 规则。

## 2. 标准任务流

用户只描述目标，不手动拼中间路径。

```text
用户需求
  -> resolve_asset_files 定位或校验输入资产
  -> 创建任务沙盒
  -> 源文件进入沙盒
  -> 执行 workflow / chain / single skill
  -> 机器中间产物写入 .info
  -> 最终输出写入沙盒根目录
  -> 写统一 REPORT.md
  -> 返回任务状态、报告路径和最终产物路径
```

三种执行模式：

| 模式 | 适用场景 | 入口 |
|---|---|---|
| Single Skill | 单个明确动作，例如导出 ABC | 具名 Tool / `execute_skill` / CLI |
| Chain | 同一 DCC 会话内多步串行 | `maya_execute_chain` |
| Workflow | 跨 DCC 或跨 pipeline 节点编排 | `pipeline_execute_workflow` |

对比、拼装、同步、材质迁移这类任务优先使用 Workflow。

## 3. 文件安全与沙盒

所有输入源文件都视为只读。禁止直接修改输入源文件；DCC 打开、修改、保存都只能针对沙盒副本。

沙盒目录：

```text
projects/{project}/{YYYYMMDD_HHMMSS}_{asset_name}/
projects/{project}/{YYYYMMDD_HHMMSS}_{asset_name}_01/
```

沙盒根目录只放用户需要直接看到的结果：

- 输入源文件的沙盒副本
- 最终输出文件
- `REPORT.md`
- `manifest.json`

所有机器中间产物必须写入：

```text
{sandbox}/.info/
```

包括 `_info.json`、`.abc`、`_materials.json`、`compare_result.json`、workflow 解析索引和其他仅供下游节点消费的结构化数据。

最终 Maya 场景使用现有版本递增规则，不追加 `_synced` 等语义后缀。正式发布到服务器由独立 `publish_asset` 类节点负责，不混入处理 workflow。

## 4. 标准执行记录

每个 step 必须返回 `core.receipt.make_receipt(...)` 标准执行记录。完整字段规则只维护在 `skills/build_pipeline_skill/SKILL.md`，运行时只依赖以下边界：

- workflow 只读取上游记录的 `output` 字段。
- `input` 只用于审计和排障，不作为下游连接数据。
- 新增或重构 skill 不得设计 `summary`、`items`、`report_content`、`report_sections` 等旧展示字段。
- 大型机器对象写 `.info` 文件，`output` 只给路径、计数和关键明细。

## 5. 报告边界

任务成功和失败都必须进入同一份 `REPORT.md`。

规则：

- `REPORT.md` 按执行顺序渲染 step 标题、`Details` 和必要错误信息。
- 报告不重新理解业务，不重组机器 JSON。
- 下游 skill 不读 Markdown 报告。
- 最终报告不得出现内部 `report:block` marker、`Raw Detail`、独立 `Input` / `Output` 大块或完整机器 JSON。

报告渲染规则见 `runtime_task_report.md`。

## 6. Workflow 编排契约

Workflow 用 JSON 声明步骤：

```json
{
  "step_id": "resolve_files",
  "skill_id": "resolve_asset_files",
  "parameters": {}
},
{
  "step_id": "compare_pre",
  "skill_id": "maya_compare_asset_in_scene",
  "source_path": "{{outputs.resolve_files.result.rig_path}}",
  "parameters": {
    "input_source": "{{outputs.export_abc.output_path}}",
    "output_path": "{{input.info_dir}}/{{outputs.resolve_files.result.rig_stem}}_pre_compare_result.json",
    "cache_group": "{{outputs.fix_hierarchy_pre.result.active_rig_root}};{{config.stages.rig.geom_roots.0}}",
    "label_source": "tex",
    "label_target": "rig"
  }
}
```

规则：

- `skill_id` 必须等于对应 `SKILL.md` 中声明的 `skill_id`。
- `step_id` 是 workflow 内部引用名。
- `parameters` 只传 skill 声明过的参数。
- DCC 根节点、项目路径、组名等项目差异使用 `{{config...}}`。
- 中间产物路径使用 `{{input.info_dir}}`。
- 上游产物通过 `{{outputs.step_id.field}}` 传递；该表达式等于上游 receipt 的 `output.field`。
- 多字段结构化输出也走 `output`，例如 `{{outputs.resolve_files.result.source_path}}`。
- 主 workflow 必须先执行 `resolve_asset_files`；缺 tex/rig 时该节点返回 `ERROR` 和 `missing_inputs`，workflow 立即中断。
- 不含 `resolve_asset_files`、但需要从资产名定位源文件的 workflow，必须在 workflow JSON 顶层声明 `source_resolution`，例如 `{"pipeline": "model", "ext_filter": [".blend"], "required": true}`。
- workflow 的全局 `source_path` 只默认打开给首个分段；后续分段必须在 step 上显式声明 `source_path`。未声明时 DCC worker 从空场景开始，供 ABC 构建等新场景型节点使用。
- Warm Pool worker 收到空 `source_path` 时必须调用对应 DCC 的新建空场景逻辑，避免复用上一任务的内存场景。
- 未显式传中间产物输出路径时，skill 只能从任务沙盒 `.info` 推导，不能回退输入文件同目录。

## 7. 参数命名规范

新 workflow 和新 skill 使用节点化参数名：

| 场景 | 参数 |
|---|---|
| 对比 source | `input_source` |
| 对比 target | `input_target` |
| source 标签 | `label_source` |
| target 标签 | `label_target` |
| source ABC | `source_abc` |
| source info | `source_info` |
| ABC 导出路径 | `abc_path` |
| compare result | `compare_result` |
| 输出路径 | `output_path` |
| asset info 输出路径 | `info_path` |
| 材质 JSON | `materials_path` |

旧命名如 `input_a/input_b`、`tex_json`、`label_a/label_b` 只做兼容，不作为新文档和新 workflow 的推荐形式。

## 8. 当前主 workflow

当前主线是 `tex_to_rig_verify_and_sync`：

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

对比、拼装、层级和 displayLayer 规则见 `compare_and_assembly_pipeline_plan.md`。

## 9. Worker 健康检查

后台提交任务前必须确认 Redis 与目标 Worker 可用。

检查分两层：

- PID 文件只证明进程仍存在。
- Celery `active_queues` 心跳必须能看到目标队列消费者，默认探测窗口为 5 秒。

队列映射：

| DCC | 队列 | Worker 节点名 |
|---|---|---|
| maya / pipeline | `dcc_queue` | `cgi_maya@%h` |
| blender | `blender_queue` | `cgi_blender@%h` |
| workflow | `workflow_queue` | `cgi_workflow@%h` |

若 PID 存活但队列心跳丢失，服务管理器应杀掉旧进程树并重新拉起对应 Worker。心跳检查只用于服务可用性判断，不给 DCC 任务本身设置硬超时。

## 10. 完成标准

一个 pipeline 任务只有满足以下条件才算完成：

- 所有输入源文件未被修改
- 所有 DCC 操作发生在沙盒副本上
- 机器中间产物全部进入 `.info`
- 最终输出按版本递增保存到沙盒根目录
- 新场景型 workflow 可从空 Maya 场景构建内容；只要显式 `save_path` 位于任务沙盒内，`save_scene` 必须允许保存未命名场景。
- 空 `source_path` 不等于“保持当前场景”，必须显式清空 DCC 会话后再执行后续 step。
- 成功或失败均写入统一 `REPORT.md`
- `manifest.json` 能索引本次任务的输入、输出、状态
- 下游 skill 只消费上游标准执行记录 `output`、JSON、ABC，不消费 Markdown
