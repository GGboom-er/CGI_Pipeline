---
skill_id: "maya_sync_rig_incremental"
name: "maya_sync_abc_to_rig_cache"
dcc: "maya"
tier: "destructive"
pairs_with:
  - "maya_compare_asset_in_scene"
  - "pipeline_compare_asset"
  - "maya_apply_materials"
  - "save_scene"
description: "在 target rig 场景中，依据前置 compare_result 和 source ABC 增量重建/更新 mesh。对 IDENTICAL/ORIG_INJECT 走快速搬运或坐标注入，对 PAIRED/UNPAIRED 走 SuperMesh 包裹重建并迁移权重/BS。"
parameters:
  compare_result:
    type: "string"
    default: ""
    description: "必填。maya_compare_asset_in_scene 或 pipeline_compare_asset 输出的 compare_result.json 路径；workflow 内也可直接传 output.compare_result 字典。sync 只消费其中 pairing_groups，不重新对比。"
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
- **compare_result 必填**: 本技能是拼装执行器，只消费前置 `compare_result.compare.pairing_groups`，不负责独立对比。输入可为 `compare_result.json` 路径，或 workflow 上游节点传下来的 `output.compare_result` 字典。
- **source_abc vs source_info**: `source_abc` 推荐保留；`PAIRED/UNPAIRED` 分支需要 ABC 的完整拓扑与 UV 来重建 mesh。新版 `compare_result` 不嵌入 `source_info`，所以必须同时提供 `source_abc` 或 `source_info`。
- **target 采集统一**: rig 侧运行时信息使用 `dccs.maya.asset_info_collector.collect_scene_info(..., include_topology=True)`，与 `maya_build_asset_info` / `maya_compare_asset_in_scene` 同源。
- **action 决策树**: sync 消费 `compare_result.compare.pairing_groups` 的 4 个 group action（`IDENTICAL` / `ORIG_INJECT` / `PAIRED` / `UNPAIRED`），算法层 7 标签只用于报告细分。
- **契约先行**: 输入解析、文件存在性、`compare_result.v1` 字段和 action 合法性先由纯 Python 契约层校验；失败时不进入 Maya 改写阶段。
- **向后兼容**: 老键名 `abc_path / tex_json` 仍被接受；新调用一律用 `compare_result / source_abc / source_info`。

### 🟢 核心逻辑 (CORE LOGIC)
- **Phase 1**: target `cache` 组全员加 `RIG_` 前缀，避免与新 mesh 命名冲突。
- **Phase 2**: 读取 `compare_result.json` 或上游 `output.compare_result` 字典作为同步指令；同步结果写入标准执行记录 `input/output`，不额外落散报告。
- **Phase 3**: 按 `pairing_groups[].action` 分发（见 `core.asset_info_schema.compare()` 契约）：
  - `IDENTICAL` → fast path：搬运 target rig mesh 到新 cache 对应层级，不改坐标
  - `ORIG_INJECT` → fast path：搬运后注入 source 坐标（点数/点序一致）
  - `PAIRED` → 多对多配对组进入 voting pool，带候选 rig 源用于定向投射权重
  - `UNPAIRED` → source 独有组进入 `_source_only`，后续用 Chamfer 自动配对或按新 mesh 处理
  - `target_only_dags` → target 独有节点进入 `_target_only`，原位保留供审核
  - 未识别 action → 判定为 compare_result 契约错误，阻断执行
- **Phase 3.5**: voting pool 里**无** `_paired_rig_dag` 的 mesh 用 Chamfer 距离自动找最近源，定向投射权重。
- **Phase 4**: SuperMesh KDTree 包裹剩余 mesh，从 ABC 纯数据重建几何、传递 UV 和权重。
- **Phase 5**: 建 Display Layer 便于审核。
- **几何-only 模式**: 项目 `rig_sync_profile.transfer_weights=false` 时，Phase 4 对 `PAIRED/UNPAIRED` 只用 ABC 重建几何，**不刷权重、不复刻 BS（含 Live BS）**，新 mesh 保持未绑定，交给绑定师手绑；`IDENTICAL/ORIG_INJECT` 仍走搬运保留原绑定，不受影响。默认 `true` 保持完整权重/BS 投射。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `om.MFnMesh.setPoints()` / `om.MFnMesh.create()`、`scipy.spatial.cKDTree`、`scipy.optimize.linear_sum_assignment`
- **契约层**: `skills.maya_sync_rig_incremental.sync_contract`，不依赖 Maya，可用普通 Python 单测覆盖。
- **BlendShape 迁移**: target 原 mesh 若有 BS 靶标，更新主几何的 delta 会同步复刻到每个 BS 靶区，保表情不坏。
- **Signed Volume 绕序修正**: ABC 纯数据建 mesh 时自动检测法线朝向，反向面自动翻转。
- **扩展**: `update_joints` 控制流预留给需要重拟合骨骼空间的角色 rig，目前默认关闭。

### 🟡 参数规则 (PARAMETERS)
- `compare_result` (string/dict): 必填 | 无 | 前置 `maya_compare_asset_in_scene` 或 `pipeline_compare_asset` 的 `output_path` 或 `output.compare_result`。
- `source_abc` (string): 推荐 | 无 | source 侧 ABC。能重建 source 独有 mesh；外部 compare_result 不带 source_info 时必须提供。
- `source_info` (string): 备选 | 无 | source 侧 `_info.json`。仅更新已有 mesh。
- `cache_group` (string): 必填 | `cache` | target rig 几何根组，workflow 应从项目配置传入。
- `dry_run` (boolean): 兼容 | false | 当前不作为只读预演入口；只看差异请运行对比 skill。
- 框架隐式参数 `source_path` (string)：必填，target 侧 rig 场景路径（Celery 层注入）。

### 🟣 输入输出记录 (RECORD)

标准执行记录 `input`:
- `source_path` (str): target 侧 rig 场景路径。
- `source_abc` (str): source 侧 ABC 路径。
- `source_info` (str): source 侧 `_info.json` 路径。
- `compare_result` (str): `compare_result.json` 路径，或 `output.compare_result`。
- `cache_group` (str): target rig 几何根组。

标准执行记录 `output`:
- `scene` (str): 当前 Maya 场景标识。
- `actions` (dict): `IDENTICAL / ORIG_INJECT / PAIRED / UNPAIRED / target_only` 执行动作计数。
- `action_summary` (str): 同步动作摘要。
- `action_items` (list): 报告 Details 使用的动作计数短明细。
- `compare_result_path` / `source_abc` / `source_info` / `cache_group` (str): 本次消费的核心输入路径/根组，便于报告定位。
- `new_node_count` (int): 本次新增 Maya 节点数量。

失败语义:
- `input_contract` / `input_files` / `compare_result_contract` / `source_load`: 进入 Maya 改写前失败，返回 `ERROR`，不修改场景。
- `target_collect` / `compare_target_match`: 已进入 undo chunk，但发现 target rig 不满足执行前提，返回 `AUDIT_FAILED` 并撤销本步。
- `execute`: 执行期异常返回 `ERROR`，关闭 undo chunk 后 `cmds.undo()` 撤销本步，并在 `recovery_hint` 指向最后的 Phase 日志。
