---
skill_id: "maya_check_asset_hierarchy"
name: "maya_check_rig_geometry_layout"
dcc: "maya"
tier: "read"
pairs_with:
  - "maya_fix_asset_hierarchy"
  - "maya_compare_asset_in_scene"
description: "纯 QC 检查：从项目配置读取当前 stage 的标准几何根，确认标准根存在、标准根下有有效 mesh，并输出层级修复节点需要消费的只读事实。"
parameters:
  stage:
    type: "string"
    default: "rig"
    description: "项目阶段。用于读取 config/{project}_config.json 中 stages[stage].geom_roots[0] 作为标准根。"
  phase:
    type: "string"
    default: "post_sync"
    description: "检查阶段。pre_sync 要求存在可供同步读取的旧绑定几何根；post_sync 要求标准 cache 根存在并有 mesh。"
  block_on_fail:
    type: "boolean"
    default: true
    description: "检查失败时是否返回 AUDIT_FAILED。false 时仍返回 SUCCESS，但 output.result.passed=false。"
  block_extra_top_nodes:
    type: "boolean"
    default: false
    description: "是否把非空额外顶层节点作为阻断项。默认 false，仅输出风险事实，避免误阻断或误删绑定系统。"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "当前 Maya 场景"
  outputs:
    - name: "result"
      type: "json"
      label: "层级 QC 结果"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读 QC**: 不创建、不移动、不重命名、不删除任何节点，不保存场景。
- **配置驱动**: 标准根只从项目配置 `stages[stage].geom_roots[0]` 读取；候选源根来自同一份配置的后续 `geom_roots` 或非标准 `cache`，只作为修复输入事实。
- **删除分级**: 顶层非 `|Group` 节点只分为 `safe_delete_top_nodes` 与 `manual_review_top_nodes`，本技能不授权删除非空绑定系统根。
- **职责收窄**: 不检查 `Group` 内部其他绑定结构，不检查 cache 外辅助 mesh，不检查 Shape/Orig、材质、权重或拓扑。

### 🟢 核心功能 (CORE FUNCTION)
- 读取项目配置得到标准几何根，例如 rig 阶段为 `|Group|Geometry|cache`，tex/mod/uv 阶段为 `|Group|cache`。
- 用 `cmds.objExists(required_root)` 判断标准根是否存在。
- 用 `cmds.listRelatives(required_root, allDescendents=True, type="mesh", fullPath=True)` 统计标准根下非 intermediate mesh 数量。
- 用 `cmds.ls(assemblies=True, long=True)` 检查顶层除默认相机外是否只有 `|Group`。
- 顶层额外非空节点默认只作为风险事实输出；只有 `block_extra_top_nodes=true` 时才阻断。
- 输出 `legacy_geo_roots`：例如 `|maYouB|geo`，供修复节点归一化为 `|Group|Geometry|RIG_geo`。
- 输出 `candidate_source_roots`：可供 `maya_fix_asset_hierarchy` 迁移的非标准几何根，例如非标准位置的 `cache`。
- 输出 `active_rig_root`：pre_sync 阶段后续 compare/sync 应读取的旧绑定几何根。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
- 入口：`skills.maya_check_asset_hierarchy.maya_check_asset_hierarchy.execute(payload)`。
- 失败统一写入标准记录 `output.result.code = "ASSET_HIERARCHY_INVALID"`；具体事实保留 `required_root_exists`、`cache_mesh_count`、`legacy_geo_roots`、`active_rig_root`、`safe_delete_top_nodes`、`manual_review_top_nodes`、`candidate_source_roots`。
- 如未来项目配置拆出 `required_geom_root`，只需要调整读取标准根的函数，检查逻辑不变。

### 🟡 参数规则 (PARAMETERS)
- `stage` (string): 选填 | `rig` | 必须存在于项目配置 `stages` 中，且该 stage 必须配置 `geom_roots[0]`。
- `phase` (string): 选填 | `post_sync` | `pre_sync` 用于 sync 前旧绑定几何根检查；`post_sync` 用于最终标准 cache 检查。
- `block_on_fail` (boolean): 选填 | `true` | true 时 QC 失败返回 `AUDIT_FAILED`；false 时返回 `SUCCESS` 但 `output.result.passed=false`。
- `block_extra_top_nodes` (boolean): 选填 | `false` | true 时非空额外顶层节点会导致 `passed=false`；false 时只进入输出报告。


**框架注入参数**（由 workflow/chain 框架自动注入，用户不需要手动传入）：
- `project` 由调度框架根据当前任务上下文自动填充。
### 🟣 标准执行记录 (RECORD)
- `input.source_path`: 当前 Maya 场景路径。
- `input.stage`: 本次检查使用的阶段。
- `input.phase`: 本次检查阶段。
- `input.required_root`: 从配置解析出的标准几何根。
- `input.block_extra_top_nodes`: 本次是否把非空额外顶层节点作为阻断项。
- `output.result.passed`: 层级 QC 是否通过。
- `output.result.code`: 通过为空；失败为 `ASSET_HIERARCHY_INVALID`。
- `output.result.required_root`: 标准几何根。
- `output.result.required_root_exists`: 标准根是否存在。
- `output.result.cache_mesh_count`: 标准根下非 intermediate mesh 数量。
- `output.result.extra_top_nodes`: 顶层除默认相机和 `|Group` 之外的 transform。
- `output.result.safe_delete_top_nodes`: 空 transform 顶层节点，可由修复节点按参数删除。
- `output.result.manual_review_top_nodes`: 非空顶层节点，默认绝不自动删除。
- `output.result.block_extra_top_nodes`: 非空额外顶层节点是否参与通过判定。
- `output.result.legacy_geo_roots`: 旧绑定几何根，例如 `|maYouB|geo`。
- `output.result.candidate_source_roots`: 层级修复节点可迁移的非标准源根。
- `output.result.active_rig_root`: 后续 compare/sync 应读取的旧绑定几何根。
- `output.result.active_rig_mesh_count`: `active_rig_root` 下有效 mesh 数量。
- `output.extra_top_nodes` / `output.manual_review_top_nodes` / `output.legacy_geo_roots` / `output.candidate_source_roots` / `output.issues`: 报告 Details 使用的精简明细；与 `output.result` 中的同名事实保持一致。
