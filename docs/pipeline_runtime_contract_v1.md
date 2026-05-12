# CGI Pipeline 运行时总规范 v1

更新时间: 2026-05-12

本文档定义 CGI Pipeline 中 AI 调用、CLI 调用、Dashboard 调用和自动任务调用共同遵守的运行时契约。后续所有 skill、workflow、MCP Tool 和报告系统都以本文档为准。

## 1. 核心目标

CGI Pipeline 的目标不是只让 AI 知道怎么执行任务，而是让同一套任务能稳定地脱离 AI 运行。

系统必须同时支持：

- AI 通过 MCP 调用
- 用户通过 CLI 调用
- Dashboard 触发任务
- 自动巡航或批处理任务触发

同一个任务在不同入口下应使用同一套 skill、workflow、receipt、audit、report 和 sandbox 规则。

## 2. 用户使用逻辑

用户只描述目标，不应手动拼中间路径。

标准流程：

```text
用户需求
  -> 定位源资产或接收显式源路径
  -> 创建任务沙盒
  -> 源文件进入沙盒
  -> 执行 workflow / chain / single skill
  -> 写入机器中间产物
  -> 写入最终输出
  -> 生成统一 Markdown 报告
  -> 返回任务结果
```

三种执行模式：

| 模式 | 适用场景 | 入口 |
|---|---|---|
| Single Skill | 单个明确动作，例如导出 ABC | 具名 Tool / `execute_skill` / CLI |
| Chain | 同一 DCC 会话内多步串行 | `maya_execute_chain` |
| Workflow | 跨 DCC 或跨 pipeline 节点编排 | `pipeline_execute_workflow` |

对比、拼装、同步、材质迁移这类任务优先使用 Workflow。

## 3. 文件安全与沙盒

### 3.1 源文件只读

所有输入源文件都视为只读。不区分盘符、服务器、工作盘或本地路径。

禁止直接修改输入源文件。任何 DCC 打开、修改、保存动作都只能针对沙盒副本。

### 3.2 沙盒目录命名

沙盒目录使用日期时间和资产名命名：

```text
projects/{project}/{YYYYMMDD_HHMMSS}_{asset_name}/
```

同一秒内同资产重复创建时追加序号：

```text
projects/{project}/{YYYYMMDD_HHMMSS}_{asset_name}_01/
projects/{project}/{YYYYMMDD_HHMMSS}_{asset_name}_02/
```

`task_id` 只作为内部调度 ID，可写入 `manifest.json` 和审计数据，不作为用户主要识别目录名。

### 3.3 沙盒根目录内容

沙盒根目录只放用户需要直接看到的任务结果：

- 输入源文件的沙盒副本
- 最终输出文件
- 统一 Markdown 报告
- `manifest.json`

### 3.4 `.info` 目录内容

所有机器中间产物必须写入：

```text
{sandbox}/.info/
```

包括：

- `_info.json`
- `.abc`
- `_materials.json`
- `compare_result.json`
- workflow 解析后的机器索引
- 其他仅供下游节点消费的结构化数据

`.info` 是机器数据目录，不是零散 debug 目录。

## 4. 最终输出命名

最终 Maya 场景使用现有版本递增规则，不额外追加 `_synced` 等语义后缀。

示例：

```text
ysj_chr_mihouwang_rig_rigMaster_v007.ma
-> ysj_chr_mihouwang_rig_rigMaster_v008.ma
```

保存只发生在沙盒内。正式发布到服务器由独立 `publish_asset` 类节点负责，不混入处理 workflow。

## 5. 报告规则

任务成功和失败都属于任务结果，必须进入同一份 Markdown 报告。

禁止把 traceback、debug txt、临时报告散落在沙盒外或沙盒内多个文件中。

报告按模块化结构组织，每个模块至少包含：

- skill / step 名称
- 输入
- 执行动作
- 输出
- 状态
- 耗时
- 错误内容
- 恢复建议或下一步建议

失败时完整错误内容写入对应模块，保证后续 debug 能从单个报告回放任务事实。

## 6. 机器数据与人工报告分离

机器消费的数据：

- JSON
- ABC
- 材质 JSON
- compare_result
- manifest

人工消费的数据：

- Markdown 报告
- receipt 摘要

原则：

- 下游 skill 不读 Markdown 报告。
- 报告不承担机器数据传递职责。
- `compare_result.json` 是机器契约，不是报告。
- `receipt.outputs` 只放稳定字段，不放长篇正文。
- 长篇说明进入 `report_content`，由统一报告系统渲染。

## 7. Skill 运行时契约

每个 skill 是一个原子 Pipeline 节点，目录结构固定：

```text
skills/{skill_id}/
  SKILL.md
  {skill_id}.py
  __init__.py
```

代码入口固定：

```python
def execute(payload: dict) -> dict:
    ...
    return make_receipt(...)
```

### 7.1 输入

Skill 从两个位置读取输入：

- `payload.source_path`: 框架层打开的 DCC 场景或主源文件
- `payload.parameters`: skill 自己声明的参数

`source_path` 是框架层字段，不必然等于算法意义上的 source。若语义不同，必须在 `SKILL.md` 的核心限制里明确说明。

### 7.2 输出

所有 skill 必须使用：

```python
core.receipt.make_receipt(...)
```

文件路径输出统一使用：

```python
outputs={"output_path": "..."}
```

`outputs` 只允许稳定机器契约字段：`output_path`、`report_path`、`result`。纯展示统计、分类数量和执行摘要写入 `summary`、`items` 或 `report_content`；需要被下游节点连接的结构化字段放入 `outputs.result.xxx`。

### 7.3 状态

常用状态：

- `SUCCESS`: 成功
- `ERROR`: 执行失败
- `BLOCKED`: 路径或权限保护阻断
- `AUDIT_FAILED`: 任务完成但质量门禁不通过
- `CHAIN_ABORTED`: 链中断
- `WORKFLOW_AUDIT_FAILED`: 工作流审计失败
- `WORKFLOW_ERROR`: 工作流异常

后台任务不进入人工 hold，不依赖 `NEEDS_ATTENTION` 继续执行。

### 7.4 异常

禁止吞异常后继续伪成功。

异常必须转成标准 receipt，并在报告模块中保留完整错误内容。

### 7.5 DCC 修改

Maya 修改类 skill 必须包裹 `cmds.undoInfo` undo chunk。

Blender 修改类 skill 必须支持 Undo / Redo 或在结束时恢复临时修改。

只读采集类 skill 不得修改场景状态。

## 8. Workflow 编排契约

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
    "cache_group": "{{config.stages.rig.geom_roots.0}}",
    "label_source": "tex",
    "label_target": "rig"
  }
}
```

规则：

- `skill_id` 必须等于 SKILL.md 中声明的 `skill_id`。
- `step_id` 是 workflow 内部引用名。
- `parameters` 只能传 skill 声明过的参数。
- DCC 根节点、项目路径、组名等项目差异使用 `{{config...}}`。
- 中间产物路径使用 `{{input.info_dir}}`。
- 上游产物通过 `{{outputs.step_id.output_path}}` 传递。
- 多字段结构化输出通过 `outputs.result` 传递，例如 `{{outputs.resolve_files.result.source_path}}`。
- 主对比/拼装 workflow 必须先执行 `resolve_asset_files`，只传资产名时由该节点查服务器最新 tex/rig；显式传 `source_path` / `extra_params.rig_path` 时由该节点校验后透传。
- 未显式传中间产物输出路径时，skill 只能从任务沙盒 `.info` 推导；不能回退输入文件同目录。

## 9. 参数命名规范

从本轮梳理开始，参数一次性统一，不再为旧命名增加新复杂度。

推荐命名：

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

旧命名如 `input_a/input_b`、`tex_json`、`label_a/label_b` 不再作为新 workflow 和文档的推荐形式。`abc_path` 仅作为 ABC 导出/导入类节点的业务参数；同步类节点使用 `source_abc`。

## 10. 对比与拼装标准数据流

```text
source DCC scene
  -> source.abc
  -> source_materials.json

target rig scene
  -> maya_compare_asset_in_scene 采集当前场景 ShapeOrig
source.abc + target rig in-memory info
  -> compare_result.json

compare_result.json + source.abc
  -> target rig 沙盒副本更新

updated target rig
  -> maya_compare_asset_in_scene 采集当前场景 ShapeOrig
  -> post_compare_result.json
  -> version-up scene
```

文件位置：

```text
sandbox/
  source scene copy
  target scene copy
  final version-up scene
  REPORT.md
  manifest.json
  .info/
    source_info.json
    source.abc
    source_materials.json
    pre_compare_result.json
    post_compare_result.json
```

## 11. 完成标准

一个 pipeline 任务只有满足以下条件才算完成：

- 所有输入源文件未被修改
- 所有 DCC 操作发生在沙盒副本上
- 机器中间产物全部进入 `.info`
- 最终输出按版本递增保存到沙盒根目录
- 成功或失败均写入统一 Markdown 报告
- `manifest.json` 能索引本次任务的输入、输出、状态
- 下游 skill 只消费 JSON/ABC/receipt outputs，不消费 Markdown
