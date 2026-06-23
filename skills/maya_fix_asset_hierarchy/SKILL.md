---
skill_id: "maya_fix_asset_hierarchy"
name: "maya_fix_rig_geometry_layout"
dcc: "maya"
tier: "destructive"
pairs_with:
  - "maya_check_asset_hierarchy"
description: "消费 maya_check_asset_hierarchy 的检查结果，按检查结果把旧绑定几何根归一化为 |Group|Geometry|RIG_geo，并只删除安全空顶层节点。"
parameters:
  check_result:
    type: "object"
    description: "必填；maya_check_asset_hierarchy 的 output.result。fix 只消费其中 required_root、legacy_geo_roots、candidate_source_roots、safe_delete_top_nodes。workflow 模板中写 {{outputs.check_hierarchy_pre.result}}。"
  remove_empty_source:
    type: "boolean"
    default: true
    description: "迁移后是否删除已经为空的源 cache 根。"
  delete_extra_top_nodes:
    type: "boolean"
    default: false
    description: "是否删除检查结果标记为 safe_delete_top_nodes 的空顶层节点；非空业务根永不自动删除。"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "当前 Maya 场景"
  outputs:
    - name: "result"
      type: "json"
      label: "层级修复结果"
category: "cleanup"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **修改场景但不保存**: 只调整当前 Maya 场景 DAG 层级，不执行 `cmds.file(save=True)`。
- **绑定检查结果**: 必须传入 `maya_check_asset_hierarchy` 的标准记录 `output.result`；本技能不重新扫描场景决定修复目标。
- **职责收窄**: 只处理层级，不检查或修复 Shape/Orig、材质、权重、拓扑、UV、命名。
- **原子回滚**: 所有修改包裹在 Maya undo chunk 中；任一步失败后关闭 chunk 并执行一次 undo 回滚。
- **绑定系统保护**: `manual_review_top_nodes` 和任何非空顶层绑定系统根绝不自动删除，避免把控制器、骨骼、约束等绑定系统一起移除。
- **锁节点处理**: 移动、重命名或删除前会尝试解锁相关节点；引用或系统原因导致失败时返回 `ERROR`。

### 🟢 核心功能 (CORE FUNCTION)
- 从 `check_result.legacy_geo_roots` 得到旧 `|*|geo` 根。
- 若场景没有 `|Group`，将旧顶层资产根重命名为 `|Group`，保留其下绑定系统。
- 创建 `|Group|Geometry`，并把旧 `geo` 迁移/重命名为 `|Group|Geometry|RIG_geo`。
- 无 legacy geo 时，才从 `check_result.candidate_source_roots` 迁移非标准 cache 的直接子节点到标准根。
- 可选删除迁移后已经为空的源 cache 根。
- 可选删除 `check_result.safe_delete_top_nodes` 中列出的空顶层节点。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
- 入口：`skills.maya_fix_asset_hierarchy.maya_fix_asset_hierarchy.execute(payload)`。
- 回执 `output.result` 包含标准根、`active_rig_root`、归一化数量、迁移数量、删除数量、源根样本。
- 推荐链路：`maya_check_asset_hierarchy(phase=pre_sync, block_on_fail=false, block_extra_top_nodes=false)` → `maya_fix_asset_hierarchy(check_result=...)` → `maya_check_asset_hierarchy(phase=pre_sync, block_on_fail=true, block_extra_top_nodes=false)` → `maya_compare_asset_in_scene` → `maya_sync_rig_incremental`。

### 🟡 参数规则 (PARAMETERS)
- `check_result` (object): 必填 | 无默认 | 必须是前置 `maya_check_asset_hierarchy` 的 `output.result`。
- `remove_empty_source` (boolean): 选填 | `true` | 只删除迁移后没有子节点的源 cache 根。
- `delete_extra_top_nodes` (boolean): 选填 | `false` | 只删除 `check_result.safe_delete_top_nodes` 列出的空节点。

### 🟣 标准执行记录 (RECORD)
- `input.source_path`: 当前 Maya 场景路径。
- `input.check_result`: 前置层级检查结果。
- `output.result.required_root`: 标准几何根。
- `output.result.active_rig_root`: 后续 compare/sync 应读取的旧绑定几何根，通常为 `|Group|Geometry|RIG_geo`。
- `output.result.normalized_rig_roots`: 本次归一化出的旧绑定几何根。
- `output.result.renamed_tops`: 顶层资产根改名记录，例如 `|maYouB -> |Group`。
- `output.result.created_groups`: 本次创建的层级节点样本。
- `output.result.source_root_count`: 参与迁移的源 cache 根数量。
- `output.result.moved_child_count`: 迁移到标准根的直接子节点数量。
- `output.result.removed_empty_source_count`: 删除的空源 cache 根数量。
- `output.result.deleted_top_node_count`: 删除的安全空顶层节点数量；非空业务根不会自动删除。
- `output.renamed_tops` / `output.normalized_rig_roots` / `output.created_groups` / `output.moved_children` / `output.deleted_top_nodes` / `output.removed_empty_sources` / `output.preserved_top_nodes` / `output.manual_review_top_nodes`: 报告 Details 使用的精简明细；下游仍以 `output.result` 为机器契约。
