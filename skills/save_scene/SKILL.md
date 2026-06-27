---
mcp_expose: true
skill_id: "save_scene"
name: "maya_save_scene_as_next_version"
dcc: "maya"
tier: "write"
pairs_with:
  - "validate_publish"
  - "rename_asset"
description: "保存当前任务沙盒中的 Maya 场景。后台 pipeline 默认保存当前已打开的沙盒副本，禁止写入沙盒外路径。"
parameters:
  save_path:
    type: "string"
    default: ""
    description: "可选。为空时保存当前已打开的沙盒场景；若填写，只允许任务沙盒内路径。"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "保存后场景"
category: "output"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **非发布节点**: 此操作不是发布(Publish)，只负责把当前任务沙盒内的 DCC 状态落盘。
- **沙盒唯一出口**: 后台 pipeline 中所有保存目标必须位于 `projects/{project}/{timestamp}_{asset}_{task_id}/` 或 `runs/{task_id}/`。
- **无人工决策**: 后台模式不会返回等待用户选择的状态；目标非法时直接返回 `BLOCKED` 并写入报告。

### 🟢 核心逻辑 (CORE LOGIC)
- 默认读取当前打开场景路径 -> 校验当前场景和目标路径均在任务沙盒 -> 必要时版本递增防覆盖 -> 调用 Maya 保存 -> 返回 `output.output_path`。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.file(rename=True)`, `cmds.file(save=True)`
- **数据留存**: 汇总报告由调度层统一生成，`save_scene` 只返回保存后的场景路径。
- **可拓展控制**: 此操作目前默认支持基于当前环境变量推导出缺省后缀为 `.ma`，如若未来有必要强行存为二进制 `.mb` 以优化读取性能，可修改内部调用扩展 `type='mayaBinary'`。

### 🟡 参数规则 (PARAMETERS)
- `save_path` (string): 选填 | 当前场景路径 | 仅允许任务沙盒内路径。后台 workflow 通常不传此参数。
