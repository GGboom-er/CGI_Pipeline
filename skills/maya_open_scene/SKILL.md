---
skill_id: "maya_open_scene"
name: "打开场景"
dcc: "maya"
tier: "destructive"
pairs_with: []
description: "打开 Maya 场景文件（.ma/.mb）或创建新空场景。支持 force 强制打开（忽略未保存修改）。"
parameters:
  file_path:
    type: "string"
    description: "要打开的场景文件路径（.ma/.mb）"
  new_scene:
    type: "boolean"
    default: false
    description: "是否创建新空场景（设为 true 时忽略 file_path）"
  force:
    type: "boolean"
    default: true
    description: "忽略未保存的修改，直接打开"
io:
  inputs:
    - name: "file"
      type: "scene_file"
      label: "Maya 场景文件"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "打开后的 Maya 场景"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **破坏性操作**：打开新文件会丢失当前未保存的场景修改（除非 force=false）。
- 仅支持 .ma 和 .mb 格式。

### 🟢 核心逻辑 (CORE LOGIC)
- `new_scene=true`: 调用 `cmds.file(new=True, force=force)` 创建空场景
- `file_path`: 检查文件存在性 → 自动识别 mayaAscii/mayaBinary → `cmds.file(open=True)`
- 打开后返回基础场景信息（mesh 数、骨骼数）

### 🔵 核心代码与扩展 (IMPLEMENTATION)
供外部服务用于预先加载大场景。支持跨平台调用。内部会在打开场景前通过 `path_guard` 脱钩保护目录的关联。

### 🟡 参数规则 (PARAMETERS)
- `file_path` (string): 选填 | 场景文件完整路径
- `new_scene` (bool): 选填 | 默认 false
- `force` (bool): 选填 | 默认 true
