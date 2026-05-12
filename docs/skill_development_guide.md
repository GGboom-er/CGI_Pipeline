# 技能开发指南（Skill Development Guide）

本文档定义了 CGI Pipeline 中新增技能的完整规范。遵循此规范可确保技能被系统正确发现、调度、执行和报告。

---

## 快速清单

新增一个技能需要完成以下 3 个文件（放在同一文件夹内）：

```
skills/{skill_id}/
├── {skill_id}.py    # 技能实现，必须包含 execute(payload) -> dict
├── SKILL.md         # 元数据（YAML frontmatter 声明 skill_id、dcc、参数）
└── __init__.py      # 内容: from .{skill_id} import execute
```

系统通过扫描 `skills/*/SKILL.md` 的 YAML frontmatter 自动注册技能，**无需手动编辑任何注册表文件**。

修改 `.py` 后无需重启 Worker（adapter 每次调用前自动 `importlib.reload`）。
新增技能后 Worker 会在下次执行前自动热重载注册表（`core/skill_registry.py` 的 `reload()`）。

---

## 1. SKILL.md 元数据

SKILL.md 的 YAML frontmatter 是技能被系统识别的唯一依据：

```yaml
---
skill_id: "my_new_skill"
name: "技能显示名称"
dcc: "maya"              # maya / blender / pipeline
description: "一句话描述功能"
parameters:
  param_name:
    type: "string"       # string / float / int / bool / list
    default: ""
    description: "参数说明"
io:
  inputs:
    - abc_path
  outputs:
    - output_path
---
```

**关键字段**：
- `skill_id`：必须与文件夹名一致
- `dcc`：决定任务路由到哪个 Worker 队列（maya→`dcc_queue`，blender→`blender_queue`，pipeline→`dcc_queue`）

---

## 2. 技能 Python 模块

### 2.1 入口函数签名

```python
def execute(payload: dict) -> dict:
```

这是唯一的入口。adapter 通过 `importlib.import_module(f'skills.{skill_id}.{skill_id}')` 动态加载并调用 `execute`。

### 2.2 payload 结构

```python
payload = {
    'task_id':    'chain-xxxx_s0',       # 系统分配
    'skill_id':   'my_skill',            # 当前技能 ID
    'source_path': 'Y:/.../asset.ma',    # 源文件路径（链引擎已打开）
    'project':    'ysj',                 # 项目代号
    'asset_name': 'xiaotianquan',        # 资产名
    'parameters': {                      # 用户传入的参数
        'scale_factor': 100.0,
        'new_scene': True,
    },
}
```

### 2.3 返回值：receipt

必须使用 `core.receipt.make_receipt()` 构造返回值：

```python
import time
from core.receipt import make_receipt, make_item

def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {})

    # ... 业务逻辑 ...

    return make_receipt(
        skill_id='my_skill',
        status='SUCCESS',              # SUCCESS / ERROR / NEEDS_ATTENTION
        start_time=t0,                 # 传入 t0，elapsed_min 自动计算
        summary_input='输入描述',       # 如文件名
        summary_action='操作描述',      # 如 "导入 ABC (缩放 100x)"
        summary_count=15,              # 产出数量（可选）
        summary_label='材质',           # 产出单位（可选）
        items=[...],                   # 明细列表（可选）
        outputs={'output_path': '...'}, # 产出路径（供下游步骤引用）
        error='错误信息',               # 仅失败时填写
    )
```

### 2.4 outputs 字段

`outputs` 中的键值对可被工作流模板引用。文件产物统一使用 `output_path`，例如技能返回 `outputs={'output_path': '/path/to/asset.abc'}`，下游步骤通过 `{{outputs.export_abc.output_path}}` 获取。统计数量写入 `summary_count` 或 `report_content`，不要放进 `outputs` 作为段间契约。

### 2.5 items 明细

用 `make_item()` 构造每一行明细，用于报告渲染：

```python
items = [
    make_item(name="body_mesh", detail="已清理 12 个零权重"),
    make_item(name="head_mesh", detail="已清理 3 个零权重"),
]
```

**截断规则**：超过 20 条时追加 `- *...及其他 N 个*`。

---

## 3. DCC 环境约束

### Maya 技能

- 运行在 mayapy 无头模式，禁止 UI 操作（`cmds.confirmDialog` 等会卡死）
- 链引擎在执行前已打开 `source_path`，技能内不需要手动打开文件
- 所有操作必须包裹在 `cmds.undoInfo(openChunk=True)` 中
- 优先使用 `maya.api.OpenMaya` 2.0 API，禁止 `MScriptUtil`
- 禁止直接 `cmds.file(save=True)` 覆盖源文件，使用 `save_scene` 技能

### Blender 技能

- 运行在 `blender --background` 模式
- 链引擎在执行前已打开 `.blend` 文件
- 禁止 UI API 调用（`bpy.ops` 中需要 context 的操作需用 override）
- 所有操作必须支持 Undo

### Pipeline 技能

- 纯 Python 计算，不依赖任何 DCC
- 复用 Maya Worker 的 `dcc_queue`（不会启动 DCC 进程）
- 适用于文件对比、路径解析、JSON 处理等

---

## 4. 链式执行规则

- 同一条链内所有技能必须属于同一个 DCC 类型
- 链引擎在第一个技能执行前统一打开文件，后续技能共享 DCC 会话
- 跨 DCC 操作必须使用工作流（`workflows/*.json`），引擎自动分段

---

## 5. 工作流定义

工作流 JSON 放在 `workflows/` 目录，格式：

```json
{
  "workflow_id": "my_workflow",
  "name": "显示名称",
  "description": "描述",
  "steps": [
    {
      "step_id": "step_1",
      "skill_id": "blender_export_abc",
      "parameters": {
        "abc_path": "{{input.info_dir}}/{{input.source_path | stem}}.abc",
        "cache_group": "{{config.stages.tex.geom_roots.0}}"
      }
    },
    {
      "step_id": "step_2",
      "skill_id": "maya_build_mesh_from_abc",
      "parameters": {"abc_path": "{{outputs.step_1.output_path}}"}
    }
  ]
}
```

**模板变量**：
- `{{outputs.step_id.field}}` — 引用前序步骤的 outputs
- `{{input.xxx}}` — 引用提交时的 extra_params
- `{{config.stages.tex.geom_roots.0}}` — 引用项目配置

---

## 6. 调试与测试

### 热更新

修改 `skills/{skill_id}/{skill_id}.py` 后无需重启任何进程，adapter 每次调用前会自动 `importlib.reload`。

### 新增技能后

Worker 在下次执行任务时会自动调用 `_reload_skill_registry()` 重新扫描 `skills/*/SKILL.md`。无需手动重启 Worker。

### 测试方法

```python
# 单技能测试（通过 MCP）
execute_skill(skill_id="my_skill", project="ysj", asset_name="test",
    source_path="Y:/.../test.ma", parameters={...})

# 链式测试
maya_execute_chain(source_path="Y:/.../test.ma", project="ysj", asset_name="test",
    skill_chain=[
        {"skill_id": "maya_build_mesh_from_abc", "parameters": {"abc_path": "..."}},
        {"skill_id": "maya_apply_materials", "parameters": {"materials_path": "..."}},
        {"skill_id": "save_scene", "parameters": {"save_path": "..."}}
    ])

# 工作流测试
execute_workflow(workflow_id="blender_to_maya_full_build",
    source_path="Y:/.../asset.blend", project="ysj", asset_name="test")

# 查询结果
maya_query_task(task_id="...")
```

### 自动巡航测试

```powershell
# 全链路巡航（tex→rig 对比+同步）
python run_auto_cruise_test.py

# Blender→Maya 构建测试
python run_build_mesh_ciweiguai.py
```

---

## 7. 常见陷阱

- **skill_id 必须与文件夹名一致** — 否则注册表找不到实现
- **禁止直接写入服务器路径** — 所有写操作通过 `save_scene` + `path_guard` 保护
- **禁止阻塞式 UI 操作** — 运行在无头模式
- **禁止 `MScriptUtil`** — 使用 OpenMaya 2.0 API
- **异常必须捕获** — 未捕获的异常会导致链式执行中断
- **路径用正斜杠** — Windows 下统一用 `/`，避免 `\\` 转义问题
- **链内不能混 DCC** — 会被引擎拒绝（CHAIN_ABORTED）
- **outputs 键名要稳定** — 下游工作流模板依赖这些键名
