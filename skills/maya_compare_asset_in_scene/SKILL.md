---
mcp_expose: true
skill_id: "maya_compare_asset_in_scene"
name: "maya_compare_abc_to_scene_geometry"
dcc: "maya"
tier: "read"
pairs_with:
  - "maya_sync_rig_incremental"
  - "maya_check_asset_hierarchy"
description: "在当前已打开的 target rig Maya 场景内采集指定 cache_group 的 ShapeOrig 几何信息，读取 source 侧 ABC 或 _info.json，调用统一 compare 算法并写出 compare_result.json。用于只看差异或为后续 maya_sync_rig_incremental 提供拼装决策。"
parameters:
  input_source:
    type: "string"
    default: ""
    description: "source 侧 ABC 或 _info.json。新 workflow 推荐传 blender_export_abc 的 output_path。"
  source_abc:
    type: "string"
    default: ""
    description: "兼容参数：source 侧 ABC。input_source 为空时使用。"
  source_info:
    type: "string"
    default: ""
    description: "兼容参数：source 侧 _info.json。input_source/source_abc 为空时使用。"
  cache_group:
    type: "string"
    description: "必填。当前 Maya target rig 场景中的几何根组，由项目配置或 workflow 传入。"
  output_path:
    type: "string"
    default: ""
    description: "compare_result.json 输出路径。为空时从任务沙盒 .info 自动推导。"
  label_source:
    type: "string"
    default: ""
    description: "source 标签，空值时从输入路径自动推断。"
  label_target:
    type: "string"
    default: "rig"
    description: "target 标签，默认 rig。"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "当前 Maya target rig 场景"
    - name: "input_source"
      type: "any_file"
      label: "source ABC 或 _info.json"
  outputs:
    - name: "compare_result"
      type: "json_file"
      label: "compare_result.json"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读场景**: 本技能只采集当前 Maya 场景信息并写出 compare_result，不修改 Maya 节点。
- **采集规则**: target 侧采集逻辑与 `maya_build_asset_info` 共用 `dccs.maya.asset_info_collector.collect_scene_info()`。
- **输入范围**: source 只接受 ABC 或 `_info.json`，不直接打开 `.ma/.mb/.blend`。
- **路径规则**: `compare_result.json` 默认写任务沙盒 `.info`；无法推导沙盒路径时返回 `ERROR`。
- **职责边界**: 本技能只输出对比结果，不执行拼装、材质、保存或 Shape 命名修复。

### 🟢 核心功能 (CORE FUNCTION)
- 读取 `input_source/source_abc/source_info`。
- 从当前 Maya 场景的 `cache_group` 采集 target rig ShapeOrig 几何。
- 调用 `core.asset_info_schema.compare(source, target)`。
- 写出标准 `compare_result.v1` JSON，并在标准执行记录 `output` 中返回可读的对比分类。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
- `execute()` 位于 `skills/maya_compare_asset_in_scene/maya_compare_asset_in_scene.py`。
- source 读取使用 `core.compare_result_io.load_info_from_path()`。
- target 采集使用 `dccs.maya.asset_info_collector.collect_scene_info()`。
- compare_result 写出使用 `core.compare_result_io.write_compare_result()`。

### 🟡 参数规则 (PARAMETERS)
- `input_source` (string): 推荐 | 无 | source 侧 ABC 或 `_info.json`。
- `source_abc` (string): 兼容 | 无 | `input_source` 为空时使用。
- `source_info` (string): 兼容 | 无 | `input_source/source_abc` 为空时使用。
- `cache_group` (string): 必填 | 无 | 当前 Maya rig 几何根组。
- `output_path` (string): 选填 | 沙盒 `.info` 自动推导 | compare_result 输出路径。
- `label_source` (string): 选填 | 自动推断 | source 标签。
- `label_target` (string): 选填 | `rig` | target 标签。

### 🟣 输入输出记录 (RECORD)

标准执行记录 `input`:
- `source_path` (str): 当前已打开的 target rig Maya 场景完整路径。
- `input_source` (str): source 侧 ABC 或 `_info.json` 完整路径。
- `output_path` (str): `compare_result.json` 目标完整路径。
- `cache_group` (str): target rig 场景中的几何根组。
- `label_source` (str): source 标签。
- `label_target` (str): target 标签。

标准执行记录 `output`:
- `output_path` (str): `compare_result.json` 绝对路径，供 `maya_sync_rig_incremental` 消费。
- `matched_total` (int): `matched_same + matched_different`。
- `matched_same` (int): source 在 target 中找到可接受配对的数量；包含算法层 `IDENTICAL` 和 `ORIG_INJECT`。
- `matched_different` (int): source 在 target 中找到配对但几何不同的数量。
- `only_source` (int): 只存在于 source 的对象数量。
- `only_target` (int): 只存在于 target 的对象数量。
- `blocking` (int): 用户视角阻断差异数量。
- `action_counts` (dict): 算法层 actionability 计数。
- `compare_sources` (list): source/target 源文件与数据路径短明细。
- `matched_same_items` / `matched_different_items` / `only_source_items` / `only_target_items` (list): 报告 Details 使用的精简明细。
- `compare_result` (dict): 标准 `compare_result.v1` 机器字典，供 workflow 直接传给 `maya_sync_rig_incremental`。

`compare_result.json` 内部可以继续保留算法字段 `paired`、`only_a`、`only_b`、`actionability`。这些字段给同步执行器和审计使用，不直接作为主报告字段。
