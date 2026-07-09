---
mcp_expose: true
skill_id: "maya_fix_shape_names"
name: "maya_fix_cache_shape_names"
dcc: "maya"
tier: "destructive"
pairs_with:
  - "validate_publish"
description: "规范化网格 Shape 的名称为 [模型名]Shape，将绑定或变形原始形重命名为 [模型名]ShapeOrig，并严格拔除没有任何连接的残渣死形节点。"
parameters:
  target_group:
    type: "string"
    default: ""
    description: "要限制检查的特定组名（如 |Group|Geometry），为空则强制检查全场景网格"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "处理后场景"
    - name: "report"
      type: "report"
      label: "修复报告 (.md)"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **强破坏性清除**: 会自动删除被识别为 `Dead Shape`（完全孤立，失去出入节点连接）的无用残留形状。
- **绑定完整性**: 重命名的过程已完全兼容绑定的 `ShapeOrig` 检测逻辑，不破坏现存蒙皮拓扑映射。

### 🟢 核心逻辑 (CORE LOGIC)
- 定位层级 -> 锁定有效目标 Transform -> 挖掘该节点下的普通 Shape 与 Intermediate Object -> 获取其父级纯名称 -> 将可见的网格赋予 `父级名 + Shape` 的标准后缀，绑定的初始形赋予 `父级名 + ShapeOrig`。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.rename`, `cmds.listConnections`, `cmds.delete`
- **重名冲突解决**: 代码自带一套哈希重命名安全退避策略，遇到同层级冲突时，不会触发引擎级别的重命名覆盖异常。
- **可拓展控制**: 虽然目前锁定于网格 (`type='mesh'`) 的后缀规范管理，如果加入其他渲染器实体，只需通过在代码的类型判断字典中扩展曲线 (`nurbsCurve`) 为 `_crvShape` 即可实现兼容。

### 🟡 参数规则 (PARAMETERS)
- `target_group` (string): 选填 | 无 | 要实施命名的指定组路径。若为空，全局搜索。
