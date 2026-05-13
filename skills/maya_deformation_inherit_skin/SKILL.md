---
skill_id: "maya_deformation_inherit_skin"
name: "形变继承 Skin"
dcc: "maya"
description: "从旧 Rig 采样蒙皮权重，按 ownership 限域生成新 mesh 的 Skin 权重，并可写回沙盒 Maya 场景。"
parameters:
  reference_rig_path:
    type: "string"
    default: ""
    description: "旧 Rig Maya 场景完整路径，用作权重采样来源"
  reference_group:
    type: "string"
    default: ""
    description: "旧 Rig 中要采样的 mesh 根节点，例如 |CDF_BaiXing_G|geo"
  target_group:
    type: "string"
    default: "|Group|Geometry|cache"
    description: "当前目标场景中要继承 Skin 的 mesh 根节点"
  mode:
    type: "string"
    default: "diagnose_only"
    description: "执行模式：diagnose_only 只输出权重和诊断；apply_skin 写回 skinCluster 并保存 output_path"
  apply_scope:
    type: "string"
    default: "missing_only"
    description: "写回范围：missing_only 只处理无 skin mesh；all 覆盖全部 target mesh"
  output_path:
    type: "string"
    default: ""
    description: "apply_skin 模式下保存的新 Maya 场景完整路径"
  info_dir:
    type: "string"
    default: ""
    description: "诊断 JSON 和权重 NPZ 输出目录；为空时优先使用 source_path 同级 .info"
  max_influences:
    type: "integer"
    default: 0
    description: "每顶点最大保留 influence 数；0 表示不裁剪"
io:
  inputs:
    - name: "source_path"
      type: "scene_file"
      label: "目标 Maya Rig 沙盒场景"
    - name: "reference_rig_path"
      type: "scene_file"
      label: "旧 Rig Maya 场景"
  outputs:
    - name: "output_path"
      type: "scene_file"
      label: "写回后的 Maya 场景或诊断 JSON"
    - name: "weights_path"
      type: "any_file"
      label: "预测权重 NPZ"
    - name: "diagnostic_path"
      type: "json_file"
      label: "ownership 与权重诊断 JSON"
category: "rig"
---

## 🔴 核心限制 (CRITICAL CONSTRAINTS)

- 只处理沙盒副本；禁止覆盖发布目录或原始源资产。
- `apply_skin` 模式必须显式提供 `output_path`，并保存为新 Maya 场景。
- 第一版只负责 Skin；不传递 BlendShape、corrective、helper rig。
- 近层物件必须先走 ownership 限域，再查询局部 field；不能只依赖全局最近点。
- `winding/layer` 不在交互式步骤逐点重复计算，后续应作为缓存 support domain 接入。

## 🟢 核心功能 (CORE FUNCTION)

```text
目标场景采集 target mesh
→ 旧 Rig 场景采集 reference mesh + skin
→ 根据 mesh 名称、几何相似、bbox、vertex count 求 owner/source candidate
→ 每个 target 使用 owner 指向的 source subset 构建 local field
→ 查询权重、生成 confidence/diagnostic
→ 可选写回 skinCluster 并保存 output_path
```

## 🔵 核心代码与扩展 (IMPLEMENTATION)

- Maya 数据采集复用 `core.maya_data_bridge.extract_mesh_geometry/extract_skin_weights`。
- 权重查询复用 `core.deformation_field.DeformationField`。
- 写回复用 `core.maya_data_bridge.inject_skin_weights`。
- 输出只使用标准执行记录 `skill/input/output/status/elapsed_sec`。
- 后续扩展点：
  - support domain / layer cache
  - VDB/GWN barrier
  - BBW/harmonic refinement
  - BS residual transfer

## 🟡 参数规则 (PARAMETERS)

- `source_path`：目标 Maya rig 沙盒场景，由执行框架打开或由本 skill 打开。
- `reference_rig_path`：旧 rig 场景，必须存在。
- `reference_group`：旧 rig 中要采样的 mesh 根节点，必填。
- `target_group`：目标场景中要查询/写回的 mesh 根节点。
- `mode`：`diagnose_only` 或 `apply_skin`。
- `apply_scope`：`missing_only` 或 `all`。
- `output_path`：`apply_skin` 必填；若文件已存在会先生成 `.bak_时间戳`。
- `weights_path` / `diagnostic_path`：固定写入 `info_dir`；若文件已存在会先生成 `.bak_时间戳`。
- `max_influences`：可选 influence 裁剪，默认不裁剪以利于验证。

## 🟣 标准执行记录 (RECORD)

成功返回：

```json
{
  "skill": "maya_deformation_inherit_skin",
  "input": {
    "source_path": "Y:/.../target.ma",
    "reference_rig_path": "Y:/.../old.ma",
    "reference_group": "|Old|geo",
    "target_group": "|Group|Geometry|cache",
    "mode": "diagnose_only"
  },
  "output": {
    "output_path": "Y:/.../.info/deformation_inherit_skin_diagnostic.json",
    "weights_path": "Y:/.../.info/deformation_inherit_skin_weights.npz",
    "diagnostic_path": "Y:/.../.info/deformation_inherit_skin_diagnostic.json",
    "mesh_count": 21,
    "predicted_count": 21,
    "applied_count": 0,
    "low_confidence_count": 0
  },
  "status": "SUCCESS",
  "elapsed_sec": 1.23
}
```
