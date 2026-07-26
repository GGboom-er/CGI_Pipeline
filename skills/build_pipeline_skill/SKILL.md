---
skill_id: "build_pipeline_skill"
name: "Skill 生成与更新规范"
dcc: "pipeline"
tier: "read"
pairs_with: []
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

后续开发先读 `docs/ai_startup/02_KNOWLEDGE_BASE.md`，再回到本文处理 skill 规则。本文只管 skill 契约；运行时边界、报告渲染、对比拼装和测试门禁分别由知识库链接到对应专项文档。

## 🔴 核心限制 (CRITICAL CONSTRAINTS)

- 不允许直接写散装脚本到 `skills/` 根目录。
- 不允许新增第二套返回结构。
- 新增或改造 skill 只设计标准执行记录的 `input/output`；旧展示字段只为历史兼容存在。
- 不允许把 Markdown 报告当作下游机器数据，也不允许业务 skill 自行写报告或 audit。
- 不允许让 sync 类 skill 只能消费临时 JSON 路径；能直接传 dict 时，应支持 dict 直连。
- 修改 DCC 场景的 skill 必须只处理沙盒副本，不能覆盖源资产或发布目录。
- 不允许写源文件同目录 fallback，不允许吞异常后返回成功。
- 需要用户决策的设计变更，先给蓝图，不直接写代码。
- 跨 skill 复用的经验、红线和避坑规则必须回归本文；单个 `skills/{skill_id}/SKILL.md` 只能记录该 skill 的本地职责、参数和实现细节，不得成为唯一知识源。
- 涉及多 skill 链路的稳定规则必须同步 `docs/ai_startup/02_KNOWLEDGE_BASE.md`；`tasks/lessons.md` 只作索引，不作规范来源。

## 🧠 跨 Skill 共享经验 (SHARED LESSONS)

这些规则适用于所有新增、改造和审查 skill。遇到同类问题时先按本节修正，再决定是否补充单个 skill 的本地说明。

### 经验回归归属

- 能影响两个及以上 skill、workflow 或数据契约的经验，必须写入本文。
- 单个 skill 的 `SKILL.md` 不得独占跨链路规则；最多写“本 skill 遵循本文某规则”的本地约束。
- `docs/ai_startup/02_KNOWLEDGE_BASE.md` 记录当前最小事实集，本文记录 skill 设计与改造细则，两者冲突时先收敛冲突再继续实现。
- `tasks/lessons.md` 只写短索引，不能把它当作后续 AI 的规范入口。

### Alembic / DAG 层级

- Alembic archive 顶层 `ABC` 是文件容器根，不是业务 DAG。任何读取 ABC 并生成 mesh key 的逻辑必须在数据层剥离该前缀，Maya 拼装结果不得出现 `|ABC` 顶层。
- 层级修复必须落在事实生产者或解析层，例如 `core.abc_reader.read_abc_as_info`；不要在下游 Maya skill 里用事后重命名掩盖错误数据。
- Maya DAG 创建后必须立即规整为 long path/fullPath；后续 parent、group、rename、mesh create 等操作不得继续依赖短名。
- 层级 fix 节点必须消费前置 check 节点输出的事实列表，不允许自行重新扫描全场景决定另一套修复目标；若 fix 过程中重命名了顶层节点，只能把 check 给出的旧 DAG 路径映射到新路径继续处理。

### 材质贴图语义

- 只有真实 color/albedo/diffuse 语义贴图才能写入 `color.type="texture"` 并连接到 Maya 材质 color。
- AO、normal/nor/nrm、roughness、metallic、specular、height、bump、displacement、opacity、mask、ORM/ARM 等非 color 语义贴图不得强行作为 color 贴图。
- 材质采集端和材质消费端都必须防守：采集端不产出错误 color texture；消费端遇到旧 JSON 中的非 color `color.path` 也不创建 file 节点。
- 找不到真实 color 贴图时，按 Blender 材质球颜色或节点链近似色输出 solid 材质；不要按 `sourceimages` 目录文件名强行猜一个 color 贴图。
- UDIM 拆分只适用于真实 color 贴图。AO/normal 等非 color 贴图不能因为带 1001/1002/1003 就拆成多个 color 材质条目。

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
| `skill` | 必须等于 `skill_id`。`skill_id` 兼容字段由 `make_receipt` 生成，不作为新契约依赖。 |
| `input` | 本次实际生效的输入参数，只用于审计和排障，不作为报告可见章节。 |
| `output` | 本次实际产物、下游连接数据和人工可展开明细的唯一来源。 |
| `status` | 执行框架判定的状态。 |
| `elapsed_sec` | 实际执行秒数。 |

### input

- 只记录实际参与执行的参数；不要记录默认未使用参数、内部 `_chain_history`、worker 临时变量。
- 路径必须是完整路径。
- 不写 `A vs B`、`A + B`、`当前文件` 这类拼接描述；每个值必须能被人或下游单独理解。
- 如果框架用 `payload.source_path` 打开文件，必须写入 `input.source_path`。
- `payload.parameters` 中实际参与执行的字段按原参数名写入 `input`。
- `payload.source_path` 是“本 step 打开的 DCC 场景”，不等于算法 source。语义冲突时必须在 SKILL.md 的核心限制中明示。

### output

- `output` 是 workflow 下游读取的唯一接口，也是报告 `Details` 的默认数据来源。
- 文件产物统一写 `output.output_path`，必须是完整路径。
- 可以同时提供语义别名，例如 `abc_path`、`materials_path`、`compare_result_path`，但下游 workflow 新引用优先使用 `output_path` 或明确声明的语义字段，不能再新增同义混乱字段。
- 无文件产物但修改当前 DCC 场景时，写 `output.scene = "current_maya_scene"` 或 `output.scene = "current_blender_scene"`。
- 数量统计直接写在 `output`，例如 `mesh_count`、`failed_count`、`material_count`。
- 业务明细按清晰字段命名写入 `output`，例如 `material_names`、`created_nodes`、`extra_top_nodes`、`issues`。
- `output` 里的 list/dict 会进入报告的 `Details`，字段名就是明细小节名；因此字段必须短、稳定、可读。
- 机器对象可以直接写入 `output`，例如 `compare_result` dict；报告渲染器只能显示摘要，不展开为噪声正文。
- 超大数据优先落 `.info` 文件，并用 `output.output_path` 暴露路径。
- 禁止把完整原始场景快照、完整 compare JSON、traceback、Markdown 正文塞进 `output`。大对象落文件，`output` 只放路径、计数、关键明细列表。

### 报告关系

- `REPORT.md` 不再渲染独立 `Input` / `Output` 章节。
- 每个 step 使用纯 Markdown 标题：`Step N/Total | skill_name | status | elapsed`。
- step 正文只显示 `Details` 和必要的错误信息，禁止依赖 raw HTML 折叠语法。
- 新 skill 要让报告看见什么，就把这些事实放进 `output` 的稳定字段。

## 1. 意图捕获

开始创建或改造 skill 前，必须确认：

| 决策项 | 必须明确的内容 |
|---|---|
| `skill_id` | 文件夹名、Python 文件名、frontmatter 必须一致 |
| `dcc` | `maya` / `blender` / `pipeline` |
| `tier` | `read` / `write` / `destructive`，MCP annotations 的唯一真相源 |
| `pairs_with` | 稳定前置、后置或常见搭配 skill_id 列表；没有则写 `[]` |
| 职责边界 | 做什么、不做什么 |
| 输入参数 | 参数名、类型、是否必填、默认值 |
| 输出字段 | `output` 中会暴露哪些字段，哪些给下游消费 |
| 文件产物 | 是否落 `.info`，是否需要 `output_path` |
| 风险等级 | 只读 / 修改沙盒场景 / 破坏性 |
| 验证方式 | 普通 Python 测试、DCC 测试或真实资产测试 |

信息不足时先问用户，不得猜。

涉及 ABC/DAG、材质、路径解析、workflow 输出契约的改动，必须先检查本文“跨 Skill 共享经验”，再写蓝图。

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
- report Details:
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
tier: "read"
pairs_with:
  - "upstream_or_downstream_skill_id"
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

字段规则：

| 字段 | 取值 | 规则 |
|---|---|---|
| `tier` | `read` | 查询、检查、解析、对比；不改源资产或当前 DCC 场景。 |
| `tier` | `write` | 生成文件、导入/创建对象、写沙盒或改变当前会话状态；不做删除/权重改写/冻结清理。 |
| `tier` | `destructive` | 任意代码执行、删除/重命名/冻结/清理、改权重、改绑定或可能破坏当前场景的操作。 |
| `pairs_with` | `[]` 或 skill_id 列表 | 只写已存在的稳定 skill_id；用于 workflow、摘要同步和路由提示，不写自然语言。 |

`mcp_server/tools_operations.py` 只能读取 `tier` 生成 MCP annotations；禁止再用 `skill_id` 前缀猜 read/write/destructive。

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
- 新增或重命名 `output` 字段时，必须同步更新相关 workflow JSON 和测试。
- 新增或重命名 `output` 字段时，还必须同步更新 `docs/ai_startup/02_KNOWLEDGE_BASE.md`、所属专项文档和 `tests/README.md` 中的门禁说明。
- 不允许让下游读取 `input`、`summary`、`items`、`report_sections` 或报告正文。

## 6. 对比类输出

对比类 skill 统一输出：

| 字段 | 含义 |
|---|---|
| `matched_total` | `matched_same + matched_different` 数量 |
| `matched_same` | 数量：source 在 target 中找到可接受配对，包含 `IDENTICAL` 和 `ORIG_INJECT` |
| `matched_different` | 数量：source 在 target 中找到配对，但几何不同 |
| `only_source` | 数量：只存在于 source |
| `only_target` | 数量：只存在于 target |
| `matched_same_items` | 可选明细列表：只放 source / target / action / layer 等核心字段 |
| `matched_different_items` | 可选明细列表：只放需要审查的核心字段 |
| `only_source_items` | 可选明细列表 |
| `only_target_items` | 可选明细列表 |

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
    "matched_same": 21,
    "matched_different": 4,
    "only_source": 0,
    "only_target": 0,
    "matched_same_items": [
      {
        "source": "bodyShape",
        "target": "body_mshShape",
        "action": "ORIG_INJECT",
        "layer": "body_Layer"
      }
    ],
    "matched_different_items": []
  }
}
```

`compare_result` 是机器对象，可以直接传给 sync；`output_path` 是审计落盘路径。不要把完整 `compare_result` 展开成报告明细，报告只看统计字段和 `*_items` 精简列表。

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

## 8. 交付检查

交付前必须确认：

- [ ] `skills/{skill_id}/SKILL.md` 存在。
- [ ] `skills/{skill_id}/{skill_id}.py` 存在。
- [ ] `skills/{skill_id}/__init__.py` re-export `execute`。
- [ ] `input` 字段只包含本次实际生效参数。
- [ ] `output` 字段能直接给报告 `Details` 和下游 skill 使用。
- [ ] `output` 字段名已在 SKILL.md 的 `io.outputs` 或标准记录章节写清楚。
- [ ] 文件路径都是完整路径。
- [ ] 无旧展示字段。
- [ ] 报告不需要额外 Markdown 才能说明本 skill 的结果。
- [ ] 若改动影响 workflow、报告、`output` 字段或测试门禁，已同步 `docs/ai_startup/02_KNOWLEDGE_BASE.md` 与对应专项文档。
- [ ] 修改 DCC 场景时有 undo / 沙盒保护。
- [ ] 有测试或明确说明未跑测试的原因。
