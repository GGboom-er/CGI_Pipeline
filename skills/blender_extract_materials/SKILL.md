---
skill_id: "blender_extract_materials"
name: "blender_extract_cache_materials"
dcc: "blender"
tier: "write"
pairs_with:
  - "maya_apply_materials"
description: "从 Blender 场景 cache 组采集 per-face 材质分配（颜色、透明度、贴图路径），UDIM 按象限拆分，输出 _materials.json。与 maya_apply_materials 配对。"
parameters:
  output_path:
    type: "string"
    default: ""
    description: "_materials.json 输出完整路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。"
  cache_group:
    type: "string"
    description: "必填。要采集的 Blender 根对象名，由项目配置或 workflow 传入。"
io:
  inputs:
    - name: "scene"
      type: "blend_file"
      label: "Blender 场景"
  outputs:
    - name: "output_path"
      type: "json_file"
      label: "_materials.json"
category: "material"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读采集**: 只能在 Blender 环境执行，输出为 JSON 文件，不会改变当前场景。
- **职责边界**: 本技能只生成面级材质 `_materials.json`。几何 `_info.json` 由 `blender_build_asset_info` 负责，ABC 由 `blender_export_abc` 负责。
- **路径规则**: workflow 必须显式传 `output_path="{{input.info_dir}}/..._materials.json"`。单技能兜底也只写任务沙盒 `.info`，不回退源文件同目录。
- **采集根组**: `cache_group` 必须由项目配置或 workflow 传入，不在技能内部猜测默认组。
- **贴图语义**: AO、normal、roughness、metallic、height、mask 等非 color 语义贴图不得写入 `color.type="texture"`。没有真实 color/albedo/diffuse 贴图时，输出 solid 材质色。

### 🟢 核心逻辑 (CORE LOGIC)

遍历 cache 组下所有 mesh，提取每个面的材质分配：
- 颜色：追踪 Principled BSDF 的 Base Color 输入，支持真实 color 贴图/纯色/节点链近似计算；非 color 贴图只作为节点链参考，不作为 Maya color 贴图输出
- 透明度：追踪 Alpha / Transmission Weight 输入
- UDIM：检测 TILED 贴图源，按 UV 象限拆分为独立材质条目

### 🔵 核心代码与扩展 (IMPLEMENTATION)

输出 `_materials.json` 格式：
```json
{
  "materials": {
    "MatName_1001": {
      "color": {"type": "texture", "path": "...", "is_udim": false},
      "alpha": {"type": "value", "value": 1.0, "semantic": "alpha"},
      "faces_by_mesh": {"cache|grp|mesh": [0, 1, 2, ...]}
    }
  }
}
```

- **下游**：`maya_apply_materials`（消费 `_materials.json`）
- **同源**：`blender_export_abc`（导出 ABC 几何）
- **输出路径推导**：优先 `parameters.output_path`，其次 `parameters.info_dir` / `payload.extra_params.info_dir`，最后 `payload.run_dir/.info` 或 `task_id` 对应任务沙盒 `.info`。

### 🟡 参数规则 (PARAMETERS)
- `output_path` (string): 选填 | 任务沙盒 `.info/{source_stem}_materials.json` | workflow 中必须显式传入。
- `cache_group` (string): 必填 | 无 | 采集根对象名，可传 `cache` 或 Maya 风格路径，代码会取最后一段在 Blender 对象表中查找。


**框架注入参数**（由 workflow/chain 框架自动注入，用户不需要手动传入）：
- `asset_name`、`project`、`run_dir`、`task_id`、`info_dir`、`extra_params`、`submitted_at` 等由调度框架根据当前任务上下文自动填充。
### 🟣 输出字段 (OUTPUTS)
- `output_path` (str): `_materials.json` 绝对路径。
