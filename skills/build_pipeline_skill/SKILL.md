---
skill_id: "build_pipeline_skill"
name: "Skill 生成与更新规范"
dcc: "pipeline"
description: "唯一的 CGI Pipeline skill 生成与更新规范文档。当用户要求新增、修改、整理、规范化 skill 时，必须先使用本 skill。"
parameters: {}
io:
  inputs: []
  outputs:
    - name: "output_path"
      type: "report"
      label: "Skill 生成与更新规范"
category: "system"
---

# Skill 生成与更新规范

本文档是 CGI Pipeline 中 **生成、更新、整理、审查 skill 的唯一规范和标准**。

用户说“写一个新 skill / 新增技能 / 改造 skill / 统一 skill 规范 / 整理 skill 输出”时，AI 必须先读取本文件，再执行后续工作。

其它文档不得另写一套 skill 规范。

## 🔴 核心限制 (CRITICAL CONSTRAINTS)

- 不允许直接写散装脚本到 `skills/` 根目录。
- 不允许新增第二套返回结构。
- 不允许继续扩展旧字段：`summary`、`items`、`report_content`、`report_sections`、`recovery_hint`。
- 不允许把 Markdown 报告当作下游机器数据。
- 不允许让 sync 类 skill 只能消费临时 JSON 路径；能直接传 dict 时，应支持 dict 直连。
- 修改 DCC 场景的 skill 必须只处理沙盒副本，不能覆盖源资产或发布目录。
- 需要用户决策的设计变更，先给蓝图，不直接写代码。

## 🟢 核心功能 (CORE FUNCTION)

本 skill 规定一个确定流程：

```text
意图捕获 -> 蓝图确认 -> 生成/修改 skill -> 契约检查 -> 报告输出 -> 经验沉淀
```

最终产物必须是一个可注册、可调用、可串联的 CGI Pipeline skill。

## 🔵 核心代码与扩展 (IMPLEMENTATION)

skill 文件结构固定：

```text
skills/{skill_id}/
├── {skill_id}.py
├── SKILL.md
└── __init__.py
```

Python 入口固定：

```python
def execute(payload: dict) -> dict:
```

必须通过 `core.receipt.make_receipt(...)` 返回标准执行记录。当前代码迁移期间如果底层 receipt 仍保留兼容字段，新增/改造 skill 的对外设计仍必须按本文标准记录设计。

## 🟡 参数规则 (PARAMETERS)

本 meta-skill 无运行时参数。它约束 AI 的创建流程，不直接处理 DCC 场景。

## 🟣 标准执行记录 (RECORD)

所有业务 skill 对外只允许一条标准执行记录：

```json
{
  "skill": "save_scene",
  "input": {
    "source_path": "Y:/.../rig_v001.ma"
  },
  "output": {
    "output_path": "Y:/.../rig_v002.ma"
  },
  "status": "SUCCESS",
  "elapsed_sec": 0.5
}
```

字段规则：

| 字段 | 规则 |
|---|---|
| `skill` | 必须等于 `skill_id` |
| `input` | 本次实际生效的输入参数 |
| `output` | 本次实际产物和可传给下游的数据 |
| `status` | 执行框架判定的状态 |
| `elapsed_sec` | 实际执行秒数 |

### input

- 只记录实际参与执行的参数。
- 路径必须是完整路径。
- 不写 `A vs B`、`A + B`、`当前文件` 这类拼接描述。
- 如果框架用 `payload.source_path` 打开文件，必须写入 `input.source_path`。
- `payload.parameters` 中实际参与执行的字段按原参数名写入 `input`。

### output

- 文件产物统一写 `output.output_path`，必须是完整路径。
- 无文件产物但修改当前 DCC 场景时，写 `output.scene = "current_maya_scene"` 或 `output.scene = "current_blender_scene"`。
- 数量统计直接写在 `output`，例如 `mesh_count`、`failed_count`、`material_count`。
- 业务明细按清晰字段命名写入 `output`。
- 机器对象可以直接写入 `output`，例如 `compare_result` dict；报告渲染器只能显示摘要，不展开为噪声正文。
- 超大数据优先落 `.info` 文件，并用 `output.output_path` 暴露路径。

## 1. 意图捕获

开始创建或改造 skill 前，必须确认：

| 决策项 | 必须明确的内容 |
|---|---|
| `skill_id` | 文件夹名、Python 文件名、frontmatter 必须一致 |
| `dcc` | `maya` / `blender` / `pipeline` |
| 职责边界 | 做什么、不做什么 |
| 输入参数 | 参数名、类型、是否必填、默认值 |
| 输出字段 | `output` 中会暴露哪些字段，哪些给下游消费 |
| 文件产物 | 是否落 `.info`，是否需要 `output_path` |
| 风险等级 | 只读 / 修改沙盒场景 / 破坏性 |
| 验证方式 | 普通 Python 测试、DCC 测试或真实资产测试 |

信息不足时先问用户，不得猜。

## 2. 蓝图格式

写代码前必须先给用户蓝图：

```markdown
## Skill 蓝图

- skill_id:
- dcc:
- category:
- 职责:
- 不负责:
- input:
- output:
- workflow 连接:
- 风险:
- 验证:
```

用户确认后再改文件。

## 3. SKILL.md 规则

frontmatter 必填：

```yaml
---
skill_id: "xxx"
name: "中文显示名"
dcc: "maya"
description: "一句话说明什么时候使用这个 skill。"
parameters:
  param_name:
    type: "string"
    default: ""
    description: "参数说明"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "output_path"
      type: "json_file"
      label: "输出文件"
category: "inspect"
---
```

正文必须包含：

- `🔴 核心限制 (CRITICAL CONSTRAINTS)`
- `🟢 核心功能 (CORE FUNCTION)`
- `🔵 核心代码与扩展 (IMPLEMENTATION)`
- `🟡 参数规则 (PARAMETERS)`
- `🟣 标准执行记录 (RECORD)`

## 4. 参数命名

- 对比类双入口：`input_source` / `input_target`，`label_source` / `label_target`。
- 同步类 source 数据：`source_abc` / `source_info`。
- 机器对比结果：`compare_result`，优先允许 dict，兼容路径由 skill 自己判断。
- 文件产物目标路径：`output_path`。
- ABC 导出目标路径：`abc_path`。
- asset info 输出路径：`info_path`。
- 材质 JSON：`materials_path`。

禁止新增 `input_a/input_b`、`tex_json`、`abcPath` 这类不清晰或旧式参数。

## 5. Workflow 串联

workflow 只读取上游 `output`：

```json
{
  "parameters": {
    "source_abc": "{{outputs.export_abc.output_path}}",
    "compare_result": "{{outputs.compare_pre.compare_result}}"
  }
}
```

规则：

- `{{outputs.step_id.field}}` 等于上游 `output.field`。
- 整串只有一个模板时保留原类型，dict/list 可以直接传给下游。
- 模板和普通文本混写时转成字符串。
- 下游 skill 不读 Markdown。

## 6. 对比类输出

对比类 skill 统一输出：

| 字段 | 含义 |
|---|---|
| `matched_total` | `matched_same + matched_different` 数量 |
| `matched_same` | source 在 target 中找到可接受配对，包含 `IDENTICAL` 和 `ORIG_INJECT` |
| `matched_different` | source 在 target 中找到配对，但几何不同 |
| `only_source` | 只存在于 source |
| `only_target` | 只存在于 target |

推荐：

```json
{
  "output": {
    "compare_result": {
      "schema_version": "compare_result.v1",
      "compare": {}
    },
    "output_path": "Y:/.../.info/pre_compare_result.json",
    "matched_total": 25,
    "matched_same": [],
    "matched_different": [],
    "only_source": [],
    "only_target": []
  }
}
```

`compare_result` 是机器对象，可以直接传给 sync；`output_path` 只是审计落盘路径。

## 7. DCC 约束

Maya：

- 修改类操作必须包裹 undo chunk。
- 优先 OpenMaya API 2.0。
- 禁止直接覆盖源文件。
- 当前场景由框架打开，skill 内不要重复打开。

Blender：

- 后台模式禁止依赖 UI context。
- 修改类操作必须支持 Undo / Redo 或只改沙盒副本。

Pipeline：

- 不依赖 DCC。
- 适合路径解析、JSON 处理、文件系统和报告重建。

## 8. 禁止事项

- 禁止返回 `summary/items/report_content/report_sections/recovery_hint` 作为新契约。
- 禁止业务 skill 写 Markdown 报告。
- 禁止业务 skill 写 audit。
- 禁止写源文件同目录 fallback。
- 禁止吞异常后返回成功。
- 禁止为了兼容旧字段继续扩大输入输出形态。

## 9. 交付检查

交付前必须确认：

- [ ] `skills/{skill_id}/SKILL.md` 存在。
- [ ] `skills/{skill_id}/{skill_id}.py` 存在。
- [ ] `skills/{skill_id}/__init__.py` re-export `execute`。
- [ ] `input` 字段能解释本次 skill 接收了什么。
- [ ] `output` 字段能直接给报告和下游 skill 使用。
- [ ] 文件路径都是完整路径。
- [ ] 无旧展示字段。
- [ ] 修改 DCC 场景时有 undo / 沙盒保护。
- [ ] 有测试或明确说明未跑测试的原因。
