---
skill_id: "maya_create_primitive"
name: "创建基础几何体"
dcc: "maya"
category: "process"
description: "快速创建一个基础的 Polygon 几何体，如立方体、球体、平面等。"
parameters:
  primitive_type:
    type: "string"
    default: "cube"
    description: "几何体类型，可选值包括 'cube', 'sphere', 'plane', 'cylinder', 'cone', 'torus'"
  name:
    type: "string"
    description: "几何体名称（可选）"
  radius:
    type: "number"
    description: "几何体半径/大小（可选）"
io:
  inputs: []
  outputs:
    - name: "geometry"
      type: "json"
      label: "创建的几何体"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
提供一个原子化接口，用于快速在 Maya 场景中创建基础几何体，无需编写长串的 exec_code 脚本。该操作会改变场景状态。

### 🟢 核心逻辑 (CORE LOGIC)
1. 提取参数。
2. 根据 `primitive_type` 分发到对应的 `cmds.polyXXX` 方法。
3. 返回创建好的几何体及其 shape 节点的名称。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
依赖 `maya.cmds`。支持 Foreground/Background。

### 🟡 参数规则 (PARAMETERS)
- `primitive_type` (string): 几何体类型，默认为 'cube'。
- `name` (string): 选填 | 几何体名称。
- `radius` (number): 选填 | 几何体半径。
