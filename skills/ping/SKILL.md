---
skill_id: "ping"
name: "心跳测试"
dcc: "maya"
tier: "read"
pairs_with: []
skip_audit: true
description: "检测 Maya Worker 是否存活，返回 Maya 版本号"
parameters: {}
io:
  inputs: []
  outputs: []
category: "system"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **绝对只读**: 无任何副作用，常作为管线工作流中确认节点进程是否崩盘或僵死的探针。

### 🟢 核心逻辑 (CORE LOGIC)
- 抓取基础运行上下文变量 `cmds.about` -> 打包时间戳发回。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.about(version=True)`
- **可拓展控制**: 此探针极为精简，如若需监控渲染服务器算力或 RAM 占用，可拓展 `psutil` 在此返回机器的 `CPU_percent` 供 Web 面板大盘调度。

### 🟡 参数规则 (PARAMETERS)
- 无参数。
