---
skill_id: "blender_build_asset_info"
name: "Blender 资产信息采集"
dcc: "blender"
description: "遍历指定 Blender 几何根组下所有 mesh，采集拓扑指纹（顶点数+世界空间坐标），输出标准 _info.json。只负责几何信息，不采集贴图或面级材质。"
parameters:
  info_path:
    type: "string"
    default: ""
    description: "_info.json 输出完整路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。"
  cache_group:
    type: "string"
    description: "必填。要采集的 Blender 根对象名，由项目配置或 workflow 传入。"
io:
  inputs:
    - name: "blend"
      type: "blend_file"
      label: ".blend 文件"
  outputs:
    - name: "info"
      type: "json_file"
      label: "_info.json"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读采集**: 只读取当前 Blender 场景，不修改对象、材质、贴图、选择状态或保存场景。
- **职责边界**: 本技能只生成几何 `_info.json`。贴图、透明度、UDIM 和面级材质分配由 `blender_extract_materials` 负责。
- **路径规则**: workflow 必须显式传 `info_path="{{input.info_dir}}/..._info.json"`。单技能兜底也只写任务沙盒 `.info`，不回退源文件同目录。
- **执行规则**: 每次任务都重新采集并写出本次 `_info.json`，不做 MTime 缓存复用。
- **采集范围**: `cache_group` 必须由项目配置或 workflow 传入，不在技能内部猜测默认组。
- **坐标对齐**: Blender Z-up / 米会转换为 Maya Y-up / 厘米：`x*100, z*100, -y*100`。

### 🟢 核心功能 (CORE FUNCTION)
- 定位 `cache_group` 根对象。
- 遍历根对象下所有 mesh。
- 使用原始 `obj.data` 读取顶点，不走 evaluated mesh，不应用修改器结果。
- 将顶点坐标转换到 Maya 对齐世界空间，保留 4 位小数。
- 写出符合 `core.asset_info_schema` 的 asset_info JSON。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
- 入口：`skills/blender_build_asset_info/blender_build_asset_info.py::execute(payload)`
- 输出路径推导：优先 `parameters.info_path`，其次 `parameters.info_dir` / `payload.extra_params.info_dir`，最后 `payload.run_dir/.info` 或 `task_id` 对应任务沙盒 `.info`。
- DAG key：从 `cache_group` 的最后一段开始构建，例如 `cache|body|bodyShape`。
- JSON 顶层仍保留空 `textures: {}`，用于兼容 `asset_info_schema`；本技能不向其中填充贴图数据。

### 🟡 参数规则 (PARAMETERS)
- `info_path` (string): 选填 | 任务沙盒 `.info/{source_stem}_info.json` | workflow 中必须显式传入。
- `cache_group` (string): 必填 | 无 | 采集根对象名，可传 `cache` 或 Maya 风格路径，代码会取最后一段在 Blender 对象表中查找。

### 🟣 输出字段 (OUTPUTS)
receipt.outputs:
- `output_path` (str): `_info.json` 绝对路径。

`_info.json`:
- `source_file`: 当前 Blender 文件路径。
- `meshes`: `{dag_path: {vertices, vert_positions}}`。
- `textures`: `{}`，仅为 schema 兼容保留。
