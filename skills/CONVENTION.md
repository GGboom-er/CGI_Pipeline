# CGI Pipeline 技能开发规范（Skill Convention）

本文档描述 CGI Pipeline 技能（skill）的文件结构、文档格式、执行合约与 receipt 约定。规范的目的是：让每个 skill 是一个**自包含、可单测、可被 AI/人类阅读并调用**的最小单元，并能被 `core/skill_registry.py` 自动注册和 Dashboard 正确展示。

---

## 1. 文件结构

每个技能是一个**自包含文件夹**，可独立打包分发：

```
skills/{skill_id}/
├── {skill_id}.py     # 执行代码（adapter 通过 skills.{id}.{id} 动态导入）
├── SKILL.md          # YAML frontmatter + 中文文档
└── __init__.py       # re-export 兼容层（from .{id} import *）
```

- `.py` **必须与文件夹同名**，adapter 严格走 `skills.{skill_id}.{skill_id}.execute` 动态导入
- `__init__.py` 内容通常就是 `from .{skill_id} import execute`
- `skills/` 根目录仅保留 `__init__.py` 和 `CONVENTION.md`（本文件）

---

## 2. SKILL.md 格式

### 完整 frontmatter 字段

```yaml
---
skill_id: "xxx"              # 必填，唯一 ID，和文件夹名一致
name: "中文显示名"             # 必填，Dashboard 卡片标题
dcc: "maya"                  # 必填，枚举：maya / blender / pipeline
description: "一句话描述。"     # 必填，AI 选 skill 时读这个
skip_audit: false            # 选填，true 表示跳过 audit 记录（仅 write_task_report 等报告类）
parameters:                  # 必填，参数声明
  param_name:
    type: "string"           # string / number / boolean / array / object
    default: ""
    description: "参数说明"
io:                          # 必填，输入输出声明（Dashboard 画线用）
  inputs:
    - name: "scene"
      type: "scene_file"     # 见下方 io.type 枚举
      label: "Maya 场景"
  outputs:
    - name: "info"
      type: "json_file"
      label: "_info.json"
category: "inspect"          # 必填，见下方 category 枚举
---
```

### io.type 枚举

| type | 含义 |
|---|---|
| `scene_file` | Maya .ma/.mb 或任意场景文件（泛指） |
| `blend_file` | .blend |
| `json_file` | 结构化 JSON（含 _info.json） |
| `jsonl_file` | audit 等按行 JSONL |
| `abc_file` | Alembic .abc |
| `report` | MD 报告 |
| `image_file` | 截图、贴图等 |
| `any_file` | 泛类型 |

### category 枚举

用于 `registry.json`/Dashboard 分类，决定节点标题色：

| category | 含义 | 典型技能 | 标题色 |
|---|---|---|---|
| `input` | 数据输入 | copy_files、fetch_* | 蓝 |
| `output` | 数据输出/保存/报告 | save_scene、rename_asset、write_task_report | 绿 |
| `process` | 场景处理 | maya_master_cleanup | 橙 |
| `convert` | 格式转换 | *_export_abc、maya_import_abc | 紫 |
| `inspect` | 检查/采集 | *_build_asset_info、check_uvsets、maya_get_scene_info | 青 |
| `material` | 材质操作 | maya_apply_materials、blender_extract_materials | 红 |
| `sync` | 数据同步 | maya_sync_rig_incremental | 深蓝 |
| `script` | 代码执行 | exec_code、blender_exec_code | 橘 |
| `system` | 系统诊断 | ping、validate_publish | 灰 |

### 必须章节

SKILL.md 正文必须包含以下四节（标题可以包含 emoji，次序可变，但四节必须齐全）：

- **🔴 核心限制 (CRITICAL CONSTRAINTS)** — 只读/破坏性/路径保护等关键约束
- **🟢 核心功能 (CORE FUNCTION)** — 做什么、怎么做的概括
- **🔵 核心代码与扩展 (IMPLEMENTATION)** — 关键函数位置、扩展点提示
- **🟡 参数规则 (PARAMETERS)** — 每个参数的必选性、默认值、取值范围

推荐再补一节：

- **🟣 输出字段 (OUTPUTS)** — receipt.outputs 字典的每个 key

---

## 3. execute() 执行合约

```python
import time
from core.receipt import make_receipt

def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {}) or {}
    # ... 业务逻辑 ...
    return make_receipt(
        skill_id='xxx',
        status='SUCCESS',            # SUCCESS / ERROR / BLOCKED / AUDIT_FAILED
        start_time=t0,
        summary_input='输入描述',
        summary_action='做了什么',
        summary_count=N,
        summary_label='单位',         # mesh / 文件 / 贴图 / 组 ...
        items=[...],                 # 见下方 items 规则
        outputs={...},               # 见下方 outputs 规则
    )
```

### payload 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | str | 任务 ID，chain/MCP 自动注入 |
| `skill_id` | str | 技能 ID |
| `source_path` | str | 源文件路径（沙盒化后） |
| `project` | str | 项目代号（如 'ysj'） |
| `asset_name` | str | 资产名 |
| `execution_mode` | str | 执行模式 (`"background"` / `"foreground"`)。由 MCP/CLI 注入，后台验证时会被白名单剥离，前台执行直连。 |
| `foreground_port` | int | 仅前台模式有效，指定目标 Maya/Blender 的 CommandPort 端口。由系统自动嗅探或由用户指定。 |
| `parameters` | dict | SKILL.md frontmatter 声明的业务参数 |

---

## 4. receipt 规范

### outputs 字段

| key | 类型 | 何时使用 | 说明 |
|---|---|---|---|
| `output_path` | `str` | 技能产出了文件 | 主要产出文件的绝对路径，**唯一路径 key** |
| `report_path` | `str` | 技能生成了报告 | MD 报告路径 |
| `result` | `dict` | `exec_code` 类技能 | 代码执行结果（非文件） |

### 规则

1. **产出文件路径只用 `output_path`**，不得自创 `abc_path`、`saved_path` 等
2. **报告路径只用 `report_path`**
3. `report_content` 是 `make_receipt()` 的顶层参数，不放入 `outputs`
4. `outputs` 只允许 `output_path` / `report_path` / `result`；统计、分类数量和执行摘要写 `summary` / `items` / `report_content`
5. 无文件产出的技能可以不设 `output_path`，但必须有 `outputs={}`

### items 字段（受影响对象清单）

清单类 skill（cleanup / check / rename / split 等）**必须**用 items 逐项列出影响的对象：

```python
items=[
    {'name': 'hair17Shape', 'detail': '移除 0.003 微量权重'},
    {'name': 'clothes3Shape', 'detail': '归一化权重总和'},
    # ...
]
```

`items` 会被 write_task_report 渲染成"受影响对象"表。缺失 items 会导致报告信息不足（"动了什么"不可回溯）。

### summary 字段

- `summary.input`：主要输入的简短描述（路径或类型）
- `summary.action`：做了什么（动词开头，例如 "→ sandbox"、"清理 3 个 mesh"）
- `summary.output_count`：输出数量（可与 `output_label` 配合，如 `75 个 pair`）
- `summary.output_label`：数量单位（"mesh"、"文件"、"贴图" 等）

---

## 5. 注册表加载

`core/skill_registry.py` 启动时遍历 `skills/*/SKILL.md`，解析 YAML frontmatter：

- 只有带 `skill_id` 字段的会被纳入注册表
- `get_skill_dcc(skill_id)` 按 frontmatter `dcc` 返回（默认 'maya'）
- `get_skip_audit_skills()` 返回所有 `skip_audit: true` 的 skill 集合

旧规范（只有 `name` 没有 `skill_id`）的 skill **不在注册表中**，仍可通过 adapter 直接导入执行，但不参与 DCC 路由判断、不在 Dashboard 展示、不做 schema 校验。

---

## 6. 关键约束

1. **同一 chain 内所有 skill 必须同属一个 dcc**（maya/blender/pipeline），chain 引擎会校验
2. **pipeline 类 skill 在主进程内直接执行**，不走 Worker/IPC
3. **报告生成只由 `write_task_report` skill 负责**，业务 skill 不得直接写 md 到磁盘
4. **所有产出文件必须落在沙盒内**（`projects/{project}/{YYYYMMDD_HHMMSS}_{asset}_{task_id}/`），chain 引擎会自动拷贝 skill 参数里的 `.ma/.mb/.blend/.json/.abc` 到沙盒
5. **不得在 skill 内写 audit**，`_write_audit` 是 chain/workflow 引擎内部行为
6. **后台任务不做人工暂停**，数据契约或 QC 不通过时返回 `AUDIT_FAILED`，由报告描述失败原因和修复建议

---

## 7. 参考样板

最完整规范的样板：
- `skills/pipeline_compare_asset/SKILL.md`（pipeline 类）
- `skills/maya_build_asset_info/SKILL.md`（maya 类，带 skip_audit）
- `skills/blender_export_abc/SKILL.md`（blender 类）
- `skills/write_task_report/SKILL.md`（output 类，skip_audit）
- `skills/copy_files/SKILL.md`（input 类）
