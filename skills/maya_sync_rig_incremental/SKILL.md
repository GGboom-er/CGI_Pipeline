---
skill_id: "maya_sync_rig_incremental"
name: "增量同步资产到绑定文件"
dcc: "maya"
description: "在 target rig 场景中，依据前置 compare_result 和 source ABC 增量重建/更新 mesh。对 IDENTICAL/ORIG_INJECT 走快速搬运或坐标注入，对 PAIRED/UNPAIRED 走 SuperMesh 包裹重建并迁移权重/BS。"
parameters:
  compare_result:
    type: "string"
    default: ""
    description: "必填。maya_compare_asset_in_scene 或 pipeline_compare_asset 输出的 compare_result.json。sync 只消费其中 pairing_groups，不重新对比。"
  source_abc:
    type: "string"
    default: ""
    description: "source 侧 ABC 文件路径。source 独有 mesh 需要完整拓扑，推荐传 ABC。"
  source_info:
    type: "string"
    default: ""
    description: "source 侧 _info.json 路径。无 ABC 时的降级路径，仅能更新已有 mesh，不能可靠创建 source 独有 mesh。"
  cache_group:
    type: "string"
    default: "cache"
    description: "target rig 几何根组。workflow 应从项目配置传入。"
  dry_run:
    type: "boolean"
    default: false
    description: "兼容旧调用参数。只看差异请使用 maya_compare_asset_in_scene，本技能执行真实拼装。"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "target rig 场景（由框架层经 source_path 打开）"
    - name: "compare_result"
      type: "json_file"
      label: "对比结果 JSON"
    - name: "source_abc"
      type: "abc_file"
      label: "source 资产 ABC"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "同步后 rig 场景"
category: "sync"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **破坏性改写**: 在 target 场景里直接更新 ShapeOrig 坐标或删/建 mesh。所有操作包裹 `undoInfo` 块，但不做备份。
- **`source_path` 语义**: Celery 框架约定 `payload.source_path` = 本次 skill 要打开的 DCC 场景。本 skill 中它承载的是 **target 侧 rig 场景**（不是 source）。这是框架层约定，调用时务必注意。
- **compare_result 必填**: 本技能是拼装执行器，只消费前置 `compare_result.compare.pairing_groups`，不负责独立对比。
- **source_abc vs source_info**: `source_abc` 推荐保留；`PAIRED/UNPAIRED` 分支需要 ABC 的完整拓扑与 UV 来重建 mesh。新版 `compare_result` 不嵌入 `source_info`，所以必须同时提供 `source_abc` 或 `source_info`。
- **target 采集统一**: rig 侧运行时信息使用 `dccs.maya.asset_info_collector.collect_scene_info(..., include_topology=True)`，与 `maya_build_asset_info` / `maya_compare_asset_in_scene` 同源。
- **action 决策树**: sync 消费 `compare_result.compare.pairing_groups` 的 4 个 group action（`IDENTICAL` / `ORIG_INJECT` / `PAIRED` / `UNPAIRED`），算法层 7 标签只用于报告细分。
- **向后兼容**: 老键名 `abc_path / tex_json` 仍被接受；新调用一律用 `compare_result / source_abc / source_info`。

### 🟢 核心逻辑 (CORE LOGIC)
- **Phase 1**: target `cache` 组全员加 `RIG_` 前缀，避免与新 mesh 命名冲突。
- **Phase 2**: 读取 `compare_result.json` 作为同步指令；同步摘要通过 `receipt.report_content` 进入统一任务 MD，不额外落散报告。
- **Phase 3**: 按 `pairing_groups[].action` 分发（见 `core.asset_info_schema.compare()` 契约）：
  - `IDENTICAL` → fast path：搬运 target rig mesh 到新 cache 对应层级，不改坐标
  - `ORIG_INJECT` → fast path：搬运后注入 source 坐标（点数/点序一致）
  - `PAIRED` → 多对多配对组进入 voting pool，带候选 rig 源用于定向投射权重
  - `UNPAIRED` → source 独有组进入 `_source_only`，后续用 Chamfer 自动配对或按新 mesh 处理
  - `target_only_dags` → target 独有节点进入 `_target_only`，原位保留供审核
  - 未识别 action → 兜底进 voting pool，不崩
- **Phase 3.5**: voting pool 里**无** `_paired_rig_dag` 的 mesh 用 Chamfer 距离自动找最近源，定向投射权重。
- **Phase 4**: SuperMesh KDTree 包裹剩余 mesh，从 ABC 纯数据重建几何、传递 UV 和权重。
- **Phase 5**: 建 Display Layer 便于审核。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `om.MFnMesh.setPoints()` / `om.MFnMesh.create()`、`scipy.spatial.cKDTree`、`scipy.optimize.linear_sum_assignment`
- **BlendShape 迁移**: target 原 mesh 若有 BS 靶标，更新主几何的 delta 会同步复刻到每个 BS 靶区，保表情不坏。
- **Signed Volume 绕序修正**: ABC 纯数据建 mesh 时自动检测法线朝向，反向面自动翻转。
- **扩展**: `update_joints` 控制流预留给需要重拟合骨骼空间的角色 rig，目前默认关闭。

### 🟡 参数规则 (PARAMETERS)
- `compare_result` (string): 必填 | 无 | 前置 `maya_compare_asset_in_scene` 或 `pipeline_compare_asset` 的输出。
- `source_abc` (string): 推荐 | 无 | source 侧 ABC。能重建 source 独有 mesh；外部 compare_result 不带 source_info 时必须提供。
- `source_info` (string): 备选 | 无 | source 侧 `_info.json`。仅更新已有 mesh。
- `cache_group` (string): 必填 | `cache` | target rig 几何根组，workflow 应从项目配置传入。
- `dry_run` (boolean): 兼容 | false | 当前不作为只读预演入口；只看差异请运行对比 skill。
- 框架隐式参数 `source_path` (string)：必填，target 侧 rig 场景路径（Celery 层注入）。

### 🟣 输出字段 (OUTPUTS)
receipt.outputs:
- `{}`：本技能不把统计塞进 outputs，避免下游把统计误当机器产物。

receipt.summary / receipt.items / receipt.report_content:
- `summary.action`: 记录 `IDENTICAL / ORIG_INJECT / PAIRED / UNPAIRED / target_only` 数量。
- `items[]`: 记录主要执行动作、降级原因与异常引用提示，自动截断到统一上限。
- `report_content`: 前置 compare_result 的同步摘要，由统一任务 MD 收纳；不额外落散报告。
