---
skill_id: "check_uvsets"
name: "UV Set 检查"
dcc: "maya"
description: "只读扫描 cache 组下所有 mesh 的 UV set 状况。报告每个 mesh 有几个 UV set、哪些有数据、哪些是空壳，并标记需要清理的 mesh。建议在执行 simplify_uvsets 之前先运行此技能查看现状。"
parameters:
  cache_group:
    type: "string"
    default: "cache"
    description: "扫描的根组名称，默认 cache"
io:
  inputs:
  outputs:
    - name: "result"
      type: "json"
      label: "UV 检查结构化结果"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读操作**: 绝对不修改任何多边形、材质节点或场景结构。
- **目标前置**: 只针对 `cache_group` 层级下非 `intermediateObject` 的有效网格。

### 🟢 核心逻辑 (CORE LOGIC)
- 定位层级 -> 提取所有合法网格 -> 查询所有 UV Set 层 -> 尝试评估该层中是否包含真实数据点 -> 生成每个节点的体检结果与违规警告清单。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.polyUVSet`, `cmds.polyEvaluate`。
- **性能优化**: 判定真实数据时并非遍历所有顶点，而是利用 `polyEvaluate(uvcoord=True)` 快速取得数据数额。
- **可拓展控制**: 当前警告依据是：超过 1 个 UV 集、缺少 `map1` 或 无 UV。如果管线后续允许多 UV 集（如第二通道做 AO），可在此核心函数的校验块中引入新的白名单判定规则。

### 🟡 参数规则 (PARAMETERS)
- `cache_group` (string): 选填 | `cache` | 指定所需进行体检的父级节点。支持自动查找 `|Group|cache` 等变体。

### 🟣 输出字段 (OUTPUTS)
- `outputs.result.total_meshes` (int): 扫描到的 mesh 数。
- `outputs.result.need_cleanup` (int): 需要清理的 mesh 数。
- `outputs.result.problems` (list): 可被下游或 AI 消费的问题清单。
