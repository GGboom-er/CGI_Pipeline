---
skill_id: "maya_set_attribute"
name: "设置属性"
dcc: "maya"
category: "process"
description: "快速更改特定节点的属性值。"
parameters:
  node:
    type: "string"
    description: "节点名称，如 'pCube1'"
  attribute:
    type: "string"
    description: "属性名称，如 'tx' 或 'translateX'"
  value:
    type: "string"
    description: "属性值，可以是数值或字符串"
  type:
    type: "string"
    description: "强制指定类型，如 'string'（当设定字符串值时通常需要指定，可选）"
io:
  inputs: []
  outputs:
    - name: "status"
      type: "json"
      label: "操作结果"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
修改场景属性，会改变模型状态。确保属性存在且未被锁定。

### 🟢 核心逻辑 (CORE LOGIC)
1. 将 `node` 和 `attribute` 拼接成全名。
2. 使用 `cmds.setAttr` 修改对应属性。
3. 捕获异常，如果属性不存在或锁定则反馈失败。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
提供一个原子化接口，用于快速在 Maya 场景中修改节点的指定属性。

### 🟡 参数规则 (PARAMETERS)
- `node` (string): 必填 | 节点名。
- `attribute` (string): 必填 | 属性名。
- `value` (any): 必填 | 新值。
- `type` (string): 选填 | 强制指定的数据类型。
