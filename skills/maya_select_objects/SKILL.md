---
skill_id: "maya_select_objects"
name: "选择对象"
dcc: "maya"
description: "在 Maya 中选择、取消选择或聚焦查看对象。支持按名称列表、通配符模式、节点类型选择，支持替换/追加/移除/清空操作，可选 Frame Selected 聚焦视口。"
parameters:
  names:
    type: "array"
    description: "要选择的对象名称列表"
  pattern:
    type: "string"
    description: "通配符模式，如 'body_*'"
  type:
    type: "string"
    description: "按节点类型选择，如 mesh/joint/camera/locator"
  action:
    type: "string"
    default: "replace"
    description: "选择模式：replace(替换) | add(追加) | remove(移除) | clear(清空)"
  frame_selected:
    type: "boolean"
    default: false
    description: "选择后是否 Frame Selected 聚焦视口"
io:
  inputs: []
  outputs:
    - name: "selection"
      type: "json"
      label: "选择结果"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- 选择操作会改变 Maya 的当前选择状态，但不修改场景数据。
- 必须提供 names、pattern、type 三者之一（除非 action=clear）。

### 🟢 核心逻辑 (CORE LOGIC)
- `names`: 按名称精确选择，自动过滤不存在的对象并报告
- `pattern`: 通配符匹配（如 `arm_*_jnt`）
- `type`: 按节点类型选择，对 shape 类型自动返回 transform 父节点
- `frame_selected`: 选择后调用 `cmds.viewFit()` 聚焦

### 🔵 核心代码与扩展 (IMPLEMENTATION)
常用于其他操作（如烘焙、导出）的前置选择步骤，也可以用于高亮某些特定元素给美术人员查看。支持 Foreground/Background。

### 🟡 参数规则 (PARAMETERS)
- `names` (array): 选填 | 对象名称列表
- `pattern` (string): 选填 | 通配符模式
- `type` (string): 选填 | 节点类型
- `action` (string): 选填 | 默认 replace
- `frame_selected` (bool): 选填 | 默认 false
