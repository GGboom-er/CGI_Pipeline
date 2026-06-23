---
skill_id: "maya_get_scene_info"
name: "获取场景总览"
dcc: "maya"
tier: "read"
pairs_with:
  - "validate_publish"
  - "maya_master_cleanup"
description: "获取 Maya 当前场景的全面信息，包括对象统计、面数、材质列表、时间轴、渲染器、引用文件、未知节点等。AI 理解场景全貌的基础能力。"
parameters: {}
io:
  inputs: []
  outputs:
    - name: "scene_info"
      type: "json"
      label: "场景信息 (JSON)"
category: "inspect"
skip_audit: true
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **纯只读操作**：不修改任何场景数据，可安全重复调用。

### 🟢 核心逻辑 (CORE LOGIC)
- 采集对象统计（mesh/joint/camera/light/curve/locator 数量）
- 计算全场景总顶点数、总面数，并输出 Top 10 面数最多的 mesh
- 列举所有用户创建的材质、相机、灯光
- 读取时间轴（帧范围、当前帧、FPS）、渲染器、线性单位
- 检测引用文件、未知节点、已加载插件数

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `maya.cmds` 的 `ls`, `polyEvaluate`, `playbackOptions`, `file`, `pluginInfo` 等
- **返回格式**: receipt 标准格式，outputs 字段包含完整的嵌套 JSON

### 🟡 参数规则 (PARAMETERS)
- 无需参数，直接调用即可获取当前场景信息。
