---
skill_id: "maya_apply_materials"
name: "maya_apply_materials_to_cache"
dcc: "maya"
tier: "write"
pairs_with:
  - "blender_extract_materials"
description: "消费 blender_extract_materials 导出的 _materials.json，为 Maya 场景中的 mesh 按面创建 lambert 材质球并赋予贴图。"
parameters:
  materials_path:
    type: "string"
    default: ""
    description: "_materials.json 文件路径（必填）"
  target_group:
    type: "string"
    default: ""
    description: "限定处理范围的组名，为空则处理全场景"
io:
  inputs:
    - name: "materials_json"
      type: "json_file"
      label: "_materials.json"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "已赋材场景"
category: "material"
---

### 🔴 核心限制 (RESTRICTIONS)

- **强依赖**：必须接收合法格式的 `_materials.json`，且场景中的 mesh 必须能根据短名或后缀成功匹配。匹配失败的面将保持默认材质。
- **贴图语义**：只允许真实 color/albedo/diffuse 类贴图连接到 lambert.color。AO、normal、roughness、metallic、height、mask 等非 color 贴图即使出现在旧 `_materials.json` 中，也必须降级为纯色材质球。
- **撤销支持**：由于涉及大批量材质节点创建和连接，所有操作必须包裹在 `cmds.undoInfo(openChunk=True)` 中。

### 🟢 核心逻辑 (CORE LOGIC)

读取 `_materials.json`（由 `blender_extract_materials` 生成，UDIM 已按象限拆分为独立条目），为场景中的 mesh 创建 lambert 材质球并按面赋予。

每个材质条目对应一个独立材质球：
- `color.type == "solid"` → 直接设置 lambert.color
- `color.type == "texture"` 且路径语义是真实 color → 创建 file 节点连接 color（is_udim=false 时不开 UDIM tiling）
- `color.type == "texture"` 但路径语义是 AO/normal 等非 color → 不创建 file 节点，按纯色材质球赋予
- `alpha.type == "value"` → 设置 transparency
- `alpha.type == "texture"` → 创建 file 节点连接 transparency

面赋予通过 `faces_by_mesh` 中的 DAG 路径匹配场景 mesh，支持短名和路径后缀两种匹配模式。

### 🔵 核心代码与扩展 (IMPLEMENTATION)

- **上游**：`blender_extract_materials`（生成 `_materials.json`）
- **兜底**：`maya_assign_udim_materials` / `maya_split_udim_materials`（Maya UV 分析模式，不依赖 Blender 数据）

### 🟡 参数规则 (PARAMETERS)
- `materials_path` (string): 必填 | `_materials.json` 文件路径。
- `target_group` (string): 选填 | 限定处理范围的组名，为空则处理全场景。
