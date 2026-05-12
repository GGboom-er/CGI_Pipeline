---
skill_id: "maya_build_mesh_from_abc"
name: "PyAlembic → Maya Mesh 构建"
dcc: "maya"
description: "使用 PyAlembic 读取 ABC 拓扑数据，在 Maya 中用 OpenMaya MFnMesh.create 纯数据构建 mesh（含 UV），还原 DAG 层级。不走 Maya import 命令。支持全量构建和按 mesh_filter 过滤构建。"
parameters:
  abc_path:
    type: "string"
    default: ""
    description: "ABC 文件绝对路径（必填）"
  mesh_filter:
    type: "array"
    default: null
    description: "DAG 路径列表，只构建匹配项。不传 = 全量构建"
  parent_group:
    type: "string"
    default: ""
    description: "构建后的顶层组名。默认还原 ABC 原始层级"
io:
  inputs:
    - name: "abc_path"
      type: "abc_file"
      label: "Alembic 文件"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "构建后的 Maya 场景"
category: "convert"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)

- 不调用 cmds.file / cmds.AbcImport，纯 OpenMaya API 数据构建
- 不修改场景中已有节点（同名节点跳过并报告冲突）
- 依赖 `core.abc_reader.read_abc_as_info` 作为数据源
- Signed Volume 绕序修正由 abc_reader 层完成，本技能信任输入数据

### 🟢 核心逻辑 (CORE LOGIC)

1. 调用 `core.abc_reader.read_abc_as_info(abc_path)` 获取全量 mesh 数据
2. 按 `mesh_filter` 过滤（不传则全量）
3. 解析 DAG 路径，用 `cmds.group(em=True)` 还原层级结构
4. 对每个 mesh 调用 `create_mesh(name, mesh_data, parent_path)`：
   - `MFnMesh.create(positions, face_counts, face_indices)` 构建几何
   - `MFnMesh.setUVs / assignUVs` 写入 UV
   - rename Shape 为规范名
5. 返回构建结果清单

### 🔵 核心代码与扩展 (IMPLEMENTATION)

- `create_mesh(name, mesh_data, parent_path="")` — 底层单 mesh 构建，供 sync_rig_incremental 等外部技能 import
- `execute(payload)` — 标准技能入口，全量/过滤构建
- **上游**：`blender_export_abc`（生成 ABC）、`core.abc_reader`（解析 ABC）
- **下游**：`maya_apply_materials`（赋予材质）
- **同源**：`maya_sync_rig_incremental`（增量模式下 import 本技能的 `create_mesh`）

### 🟡 参数规则 (PARAMETERS)
- `abc_path` (string): 必填 | ABC 文件绝对路径。
- `mesh_filter` (array): 选填 | DAG 路径列表，只构建匹配项。不传 = 全量构建。
- `parent_group` (string): 选填 | 构建后的顶层组名。默认还原 ABC 原始层级。
