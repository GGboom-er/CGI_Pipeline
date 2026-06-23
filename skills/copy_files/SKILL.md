---
skill_id: "copy_files"
name: "pipeline_stage_file_to_sandbox"
dcc: "pipeline"
tier: "write"
pairs_with:
  - "resolve_asset_files"
skip_audit: true
description: "通用文件拷贝：source → destination，文件或目录均可。纯文件系统操作，不启动任何 DCC。"
parameters:
  source:
    type: "string"
    default: ""
    description: "源路径（文件或目录的完整路径）"
  destination:
    type: "string"
    default: ""
    description: "目标路径（文件或目录的完整路径）"
  overwrite:
    type: "boolean"
    default: false
    description: "目标已存在时是否覆盖"
io:
  inputs:
    - name: "source"
      type: "any_file"
      label: "源路径"
  outputs:
    - name: "destination"
      type: "any_file"
      label: "目标路径"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **安全沙盒拦截**: 对所有拷贝目标路径强制应用 `path_guard`。任何试图写入网络生产大盘的操作都会被直接拒绝。
- **纯粹执行**: 无需启动任何重量级的 DCC 进程 (Maya / Blender)，完全在后台常驻服务内通过异步处理。

### 🟢 核心逻辑 (CORE LOGIC)
- 检查源文件/目录是否存在 → 执行安全验证 → 使用递归算法深度复制目录树或直接拷贝单个文件 → 汇总已移动和跳过的文件及体积输出回执。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: Python 内置 `shutil.copy2` 和 `shutil.copytree`。
- **元数据保留**: `copy2` 确保修改时间、访问时间和文件权限标签的完整带入。
- **可拓展控制**: 虽然目前默认全部递归覆盖，但未来可以在代码扩展出正则模式匹配、扩展名过滤 (`ignore=shutil.ignore_patterns`) 实现忽略 `.DS_Store` 或缓存文件的功能。

### 🟡 参数规则 (PARAMETERS)
- `source` (string): 必填 | 无 | 要移动的源路径，可以是目录或单个文件。
- `destination` (string): 必填 | 无 | 写入目标路径。
- `overwrite` (bool): 选填 | `false` | 如果目标存在是否强行覆盖。默认不覆盖以防止数据意外丢失。
