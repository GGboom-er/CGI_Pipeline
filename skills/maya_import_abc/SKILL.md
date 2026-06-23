---
skill_id: "maya_import_abc"
name: "导入 ABC 缓存"
dcc: "maya"
tier: "write"
pairs_with:
  - "blender_export_abc"
  - "maya_export_abc"
description: "将 Alembic (.abc) 文件导入 Maya 场景，支持坐标系缩放对齐（默认 100x Blender→Maya）并冻结变换。"
parameters:
  abc_path:
    type: "string"
    default: ""
    description: "ABC 文件的完整路径（必填）"
  scale_factor:
    type: "number"
    default: 100.0
    description: "导入后缩放系数，0 表示不缩放"
  new_scene:
    type: "boolean"
    default: true
    description: "是否先新建空场景再导入"
io:
  inputs:
    - name: "abc"
      type: "abc_file"
      label: ".abc 文件"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "导入后场景"
category: "convert"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **非只读**: 执行将把文件内容直接灌入当前激活的场景空间内，新增大量层级节点。
- **历史记录**: 将强制载入所有 Alembic 携带的面组集与动画曲线。

### 🟢 核心逻辑 (CORE LOGIC)
- 验证外部 ABC 存在性 -> 激活 Maya `AbcImport` 库 -> 解析并合并到场景空间 -> 自动推演 Blender(默认 `m`) 到 Maya(默认 `cm`) 的计量单位差 -> 在节点最顶端应用 `scale_factor` 进行放缩修复对齐。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.loadPlugin('AbcImport')`, `cmds.AbcImport(mode='import')`
- **缩放修复**: 通过侦测 `cmds.currentUnit`，在发现场景单位配置为 `cm` 时，默认给传入的顶层组缩放值乘以 100。
- **可拓展控制**: 针对导入方式，目前调用的是模式 `mode='import'`，若管线未来需求更轻量的检视系统，可考虑在此拓展中改为引入 `mode='open'`（直接覆盖场景）或是只导入层级结构。

### 🟡 参数规则 (PARAMETERS)
- `abc_path` (string): 必填 | 无 | 要引入的 Alembic 文件路径。
- `scale_factor` (number): 选填 | `100.0` | 若手动设定，则会强制对生成的组施加统一的轴向缩放倍率；传 0 表示不缩放。
