---
skill_id: "maya_get_object_info"
name: "查询对象信息"
dcc: "maya"
tier: "read"
pairs_with: []
description: "获取 Maya 场景中单个对象的详细信息：变换矩阵、包围盒、mesh 统计（面/顶点/边/三角形/UV集）、材质列表、修改器历史、骨骼朝向、相机参数等。"
parameters:
  object_name:
    type: "string"
    description: "要查询的对象名称"
    required: true
io:
  inputs: []
  outputs:
    - name: "object_info"
      type: "json"
      label: "对象信息 (JSON)"
category: "inspect"
skip_audit: true
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **纯只读操作**：不修改任何场景数据。
- 对象必须存在于当前场景中，否则返回 ERROR。

### 🟢 核心逻辑 (CORE LOGIC)
- Transform 属性：世界坐标位移/旋转/缩放、枢轴点
- 包围盒：min/max/size 三维尺寸
- Mesh 统计：顶点数、面数、边数、三角形数、UV 集列表
- 材质：Shading Group 绑定的材质球名称和类型
- 修改器历史：所有 deformer 和构造历史节点（最多 20 个）
- Joint 专属：orientation、radius
- Camera 专属：focal_length、near/far clip

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `maya.cmds.xform`, `polyEvaluate`, `listHistory`, `listConnections`
- 自动识别节点类型，返回该类型的专属属性

### 🟡 参数规则 (PARAMETERS)
- `object_name` (string): 必填 | 要查询的对象名称，支持短名称和长路径。
