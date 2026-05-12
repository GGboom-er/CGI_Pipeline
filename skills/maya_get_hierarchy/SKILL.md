---
skill_id: "maya_get_hierarchy"
name: "获取层级结构"
dcc: "maya"
category: "process"
description: "获取场景中特定节点或组下的层级结构列表。"
parameters:
  node:
    type: "string"
    description: "节点名称，为空则返回顶层所有节点"
  full_path:
    type: "boolean"
    default: true
    description: "是否返回完整 DAG 路径，默认为 True"
io:
  inputs: []
  outputs:
    - name: "hierarchy"
      type: "json"
      label: "层级结构数据"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
只读操作，不会修改场景状态。

### 🟢 核心逻辑 (CORE LOGIC)
1. 使用 `cmds.listRelatives(allDescendents=True)` 或 `cmds.ls` 提取子节点。
2. 过滤结果并返回树状结构或扁平列表。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
提供一个原子化接口，帮助 AI 在不编写循环的情况下，快速拉取指定组内的所有子层级结构，用于结构分析和上下文构建。

### 🟡 参数规则 (PARAMETERS)
- `node` (string): 选填 | 节点名称，为空则返回顶层。
- `full_path` (boolean): 选填 | 默认 true。
