---
skill_id: "resolve_asset_files"
name: "pipeline_resolve_tex_rig_paths"
dcc: "pipeline"
description: "根据项目配置解析资产 source tex 文件与 target rig 文件。支持只传资产名自动查服务器最新版本，也支持传入明确文件路径直接透传。"
parameters:
  category:
    type: "string"
    default: "chr"
    description: "资产类型，如 chr/prp/env。为空时优先读取 input.category，最后回退 chr。"
  source_stage:
    type: "string"
    default: "tex"
    description: "source 侧阶段，默认 tex。"
  source_task:
    type: "string"
    default: ""
    description: "source 侧 task；为空时使用项目配置中的 primary_task。"
  source_extensions:
    type: "string"
    default: ".blend"
    description: "source 侧允许扩展名，逗号分隔。"
  rig_stage:
    type: "string"
    default: "rig"
    description: "target rig 阶段，默认 rig。"
  rig_task:
    type: "string"
    default: ""
    description: "target rig task；为空时使用项目配置中的 primary_task。"
  rig_extensions:
    type: "string"
    default: ".ma,.mb"
    description: "target rig 允许扩展名，逗号分隔。"
io:
  inputs:
    - name: "asset_name"
      type: "any_file"
      label: "资产名或显式路径输入"
  outputs:
    - name: "result"
      type: "any_file"
      label: "解析结果字典"
category: "input"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读节点**: 只解析路径与检查文件存在，不复制、不写入、不打开 DCC。
- **双模式入口**: 调用方可只传 `asset_name`，也可通过 `source_path` / `extra_params.source_path` / `extra_params.rig_path` 提供明确路径。明确路径存在时优先使用，不再搜索服务器。
- **失败即阻断**: source 或 rig 任一侧无法解析到存在文件时返回 `ERROR`，不猜测、不降级到旧缓存。
- **下游契约**: 所有路径放在 `outputs.result` 中，下游 workflow 通过 `{{outputs.resolve_files.result.source_path}}` 和 `{{outputs.resolve_files.result.rig_path}}` 使用。

### 🟢 核心功能 (CORE FUNCTION)
- 读取 `project_config` → 按 `category/asset/stage/task` 查找最新版本 → 输出 source tex 文件与 target rig 文件。
- 如果调用方传了明确文件路径，则验证存在和扩展名后直接输出。
- 同时输出 stem、stage、task、version 等调试信息，方便报告定位“到底用了哪两个文件”。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
- 入口：`skills/resolve_asset_files/resolve_asset_files.py::execute(payload)`。
- 路径规则复用 `core.asset_resolver.AssetResolver` 和 `config/{project}_config.json`。
- 不写 `output_path`，因为本节点没有机器中间文件；结构化结果放入 `outputs.result`。

### 🟡 参数规则 (PARAMETERS)
- `category` (string): 选填 | `chr` | 资产类型。
- `source_stage` (string): 选填 | `tex` | source 侧阶段。
- `source_task` (string): 选填 | 项目配置 primary_task | source 侧 task。
- `source_extensions` (string): 选填 | `.blend` | source 侧允许扩展名。
- `rig_stage` (string): 选填 | `rig` | target 侧阶段。
- `rig_task` (string): 选填 | 项目配置 primary_task | target rig task。
- `rig_extensions` (string): 选填 | `.ma,.mb` | rig 侧允许扩展名。

### 🟣 输出字段 (OUTPUTS)
receipt.outputs:
- `result.source_path` (str): source tex 文件路径。
- `result.rig_path` (str): target rig 文件路径。
- `result.source_stem` / `result.rig_stem` (str): 去扩展名文件名，用于 `.info` 产物命名。
- `result.source_version` / `result.rig_version` (int): 从文件名解析到的版本号，无法解析时为 0。
- `result.explicit_source` / `result.explicit_rig` (bool): 是否由调用方显式传入。
