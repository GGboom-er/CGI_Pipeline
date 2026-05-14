# CGI Pipeline 运行时总规范 v1

更新时间: 2026-05-13

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

## 5. 标准执行记录与报告规则

任务成功和失败都属于任务结果，必须进入同一份 Markdown 报告。

禁止把 traceback、debug txt、临时报告散落在沙盒外或沙盒内多个文件中。

从本规范开始，报告不再重新理解业务、不再重组自然语言结论。完整 skill 输入输出契约只维护在 `skills/build_pipeline_skill/SKILL.md`，本节只规定运行时边界：

- 每个 step 必须返回 `make_receipt(...)` 标准执行记录。
- workflow 只读取上游记录的 `output` 字段。
- `REPORT.md` 只按记录顺序渲染 step 折叠头、`Details` 和必要错误信息。
- 旧展示字段只用于历史兼容，不得作为新增 skill 的设计入口。

## 6. 机器数据与人工报告分离

机器消费的数据：

- JSON
- ABC
- 材质 JSON
- compare_result
- manifest

人工消费的数据：

- Markdown 报告
- 标准执行记录

原则：

- 下游 skill 不读 Markdown 报告。
- 报告不承担机器数据传递职责。
- `compare_result.json` 是机器契约，不是报告正文。
- Workflow 下游只消费上游标准记录的 `output` 字段。
- 例如 `{{outputs.compare_pre.output_path}}` 指向的是 `compare_pre` 记录里的 `output.output_path`。

## 7. Skill 运行时契约

完整 skill 构建规则只维护在 `skills/build_pipeline_skill/SKILL.md`。本运行时总规范不再复制 skill 文件结构、`SKILL.md`、`execute(payload)`、标准执行记录、DCC 约束等细节。

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
  "source_path": "{{outputs.resolve_files.rig_path}}",
  "parameters": {
    "input_source": "{{outputs.export_abc.output_path}}",
    "output_path": "{{input.info_dir}}/{{outputs.resolve_files.rig_stem}}_pre_compare_result.json",
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
- 多字段结构化输出直接通过上游记录的 `output` 传递，例如 `{{outputs.resolve_files.source_path}}`。
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

`maya_compare_asset_in_scene` 必须继续输出 `compare_result.json`。原因是该文件是 `maya_sync_rig_incremental` 的机器输入，记录完整 DAG、配对关系和动作类型；它不是给人工报告直接阅读的正文。

`compare_result.json` 内部可以继续保留算法字段，例如 `paired`、`only_a`、`only_b`、`actionability`。这些字段服务于同步和审计，不作为主报告字段名。报告只展示标准执行记录 `output` 中整理后的四类字段。

对比 skill 的标准执行记录中，`output` 只展示可读且可串联的核心字段：

```json
{
  "skill": "maya_compare_asset_in_scene",
  "input": {
    "source_path": "Y:/.../ysj_chr_maYouA_rig_rigMaster_v001.ma",
    "input_source": "Y:/.../.info/ysj_chr_maYouA_tex_texMaster_v001.abc",
    "output_path": "Y:/.../.info/ysj_chr_maYouA_rig_rigMaster_v001_pre_compare_result.json",
    "cache_group": "|Group|Geometry|cache;|*|geo",
    "label_source": "tex",
    "label_target": "rig"
  },
  "output": {
    "output_path": "Y:/.../.info/ysj_chr_maYouA_rig_rigMaster_v001_pre_compare_result.json",
    "matched_total": 25,
    "matched_same": 24,
    "matched_different": 1,
    "only_source": 0,
    "only_target": 0,
    "matched_same_items": [
      {
        "source": "maYouA_M_eyebrow1Shape",
        "target": "eyebrow_mshShape",
        "action": "ORIG_INJECT"
      }
    ],
    "matched_different_items": []
  },
  "status": "SUCCESS",
  "elapsed_sec": 1.2
}
```

四类对比输出定义：

| 字段 | 含义 |
|---|---|
| `matched_same` | 数量：source 在 target 中找到可接受配对；包含 `IDENTICAL` 和 `ORIG_INJECT`，不算阻断问题 |
| `matched_different` | 数量：source 在 target 中找到配对，但几何不同，需要后续处理或审查 |
| `only_source` | 数量：只存在于 source，target 中没有对应对象 |
| `only_target` | 数量：只存在于 target，source 中没有对应对象 |
| `matched_same_items` | 可选明细：只放 source / target / action / layer 等核心字段 |
| `matched_different_items` | 可选明细：只放需要审查的核心字段 |
| `only_source_items` | 可选明细 |
| `only_target_items` | 可选明细 |

`matched_total` 只是 `matched_same + matched_different` 的数量。不要在报告里使用含义模糊的 `paired` / `identical` 作为主字段。

旧字段解释：

| 旧字段 | 实际含义 | 新报告字段 |
|---|---|---|
| `paired` | source 与 target 成功建立配对的总数；例如 `paired: 25` 表示 25 个 source mesh 找到了 target mesh | `matched_total` |
| `identical` | 已配对且几何可直接接受的对象列表；包含算法层 `IDENTICAL` 和 `ORIG_INJECT` | `matched_same_items`，数量进入 `matched_same` |
| `only_a` | 只在 source 侧存在 | `only_source_items`，数量进入 `only_source` |
| `only_b` | 只在 target 侧存在 | `only_target_items`，数量进入 `only_target` |

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

## 11. Worker 健康检查

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

若 PID 存活但队列心跳丢失，服务管理器应杀掉旧进程树并重新拉起对应 Worker。

可观测入口：

- `pipeline_service_status`: 查看 Redis、PID 和队列心跳。
- `pipeline_restart_worker`: 手动重启指定 Worker。

心跳检查只用于服务可用性判断，不给 DCC 任务本身设置硬超时。

## 12. 完成标准

一个 pipeline 任务只有满足以下条件才算完成：

- 所有输入源文件未被修改
- 所有 DCC 操作发生在沙盒副本上
- 机器中间产物全部进入 `.info`
- 最终输出按版本递增保存到沙盒根目录
- 成功或失败均写入统一 Markdown 报告
- `manifest.json` 能索引本次任务的输入、输出、状态
- 下游 skill 只消费上游标准执行记录 `output`、JSON、ABC，不消费 Markdown
