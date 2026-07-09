---
mcp_expose: true
skill_id: "blender_exec_code"
name: "Blender 动态代码执行"
dcc: "blender"
tier: "destructive"
pairs_with: []
skip_audit: true
description: "在 Blender Python 环境中执行任意代码并返回结果。代码中将结果赋值给 result 变量即可。链引擎打开文件时内部使用。"
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
    - name: "blend"
      type: "blend_file"
      label: ".blend 文件"
  outputs:
    - name: "blend"
      type: "blend_file"
      label: "处理后 .blend"
category: "script"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **沙盒隔离**: 禁止调用原生 `open()` 向受保护的网络映射驱动器执行写入。
- **无界面调用**: 该技能运行于 `blender -b` 后台模式，禁止调用任何 `bpy.ops.*` 需要 UI 上下文的 API。

### 🟢 核心逻辑 (CORE LOGIC)
- 提取字符串代码 -> 挂载 `builtins` 沙盒及 `bpy` 库 -> 通过内置 `exec()` 编译执行 -> 从执行空间抓取 `result` 字典并格式化为标准回执。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: Python 内置 `exec` 函数。
- **结果解析**: `result` 字典会被自动解构，支持 int/float/list/dict 转为项计数，长字符串转为 MD 报告。
- **可拓展控制**: 脚本中可通过覆盖或新增局部变量实现与工作流系统的数据交互；对于无法通过 `result` 返回的二进制数据，可以在代码内部调用其他工具写出至 tmp 目录。

### 🟡 参数规则 (PARAMETERS)
- `code` (string): 必填 | 无 | 要执行的符合语法规范的 Python 代码。
- `description` (string): 选填 | `(未描述)` | 代码用途的简短描述，用于日志追踪。
