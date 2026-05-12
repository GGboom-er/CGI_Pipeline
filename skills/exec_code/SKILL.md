---
skill_id: "exec_code"
name: "动态代码执行"
dcc: "maya"
skip_audit: true
description: "在当前 Maya 会话中执行任意 Python 代码并返回结果。代码中将结果赋值给 result 变量即可。"
parameters:
  code:
    type: "string"
    default: ""
    description: "待执行的 Python 代码字符串"
  description:
    type: "string"
    default: ""
    description: "代码用途描述（用于审计日志）"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "处理后场景"
category: "script"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **写保护**: 内置自定义 `open()` 钩子，拦截对生产受保护路径的写入操作。

### 🟢 核心逻辑 (CORE LOGIC)
- 提取代码字符串 -> 注入 `builtins` 和受保护环境 -> 通过 `compile()` 和 `exec()` 执行 -> 捕获命名空间内的 `result` 变量 -> 结构化输出为 receipt。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: 原生 Python `exec()` 和 AST 编译。
- **结果解析**: 自动将 `result` 字典中的基本类型转化为 dashboard 的 Items 列表，长字符串转化为内嵌报告。
- **可拓展控制**: 可通过在代码中返回特定结构的 `result` 字典来定制前端界面的报告展示效果（无需修改本技能源码）。

### 🟡 参数规则 (PARAMETERS)
- `code` (string): 必填 | 无 | 要执行的 Python 脚本。必须将输出赋给 `result` 变量。
- `description` (string): 选填 | `(未描述)` | 记录在任务审计中的描述。
