---
skill_id: "blender_export_abc"
name: "Blender 导出 ABC"
dcc: "blender"
description: "选中指定几何根组，禁用修改器，导出 Alembic (.abc)。只管 ABC 几何导出，不生成 _info.json 或 _materials.json。"
parameters:
  abc_path:
    type: "string"
    default: ""
    description: "ABC 输出完整路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。"
  cache_group:
    type: "string"
    default: ""
    description: "要导出的组名（为空则从项目推断即可）"
io:
  inputs:
    - name: "blend"
      type: "blend_file"
      label: ".blend 文件"
  outputs:
    - name: "abc"
      type: "abc_file"
      label: "导出的 .abc"
category: "convert"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **非破坏性导出**: 导出过程中会临时禁用特定修改器，但在导出完成后会利用撤销机制回退，确保场景无变更。
- **职责单一**: 本技能不包含信息或材质采集。几何指纹由 `blender_build_asset_info` 生成，面级材质由 `blender_extract_materials` 生成。
- **路径规则**: workflow 必须显式传 `abc_path="{{input.info_dir}}/... .abc"`。单技能兜底也只写任务沙盒 `.info`，不回退源文件同目录。

### 🟢 核心逻辑 (CORE LOGIC)
- 定位 `cache_group` 根对象 -> 选择其层级内对象 -> 禁用 mesh 修改器显示 -> 配置 Alembic 导出参数 -> 写入 `.abc` 文件。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `bpy.ops.wm.alembic_export`
- **输出路径推导**: 优先 `parameters.abc_path`，其次 `parameters.info_dir` / `payload.extra_params.info_dir`，最后 `payload.run_dir/.info` 或 `task_id` 对应任务沙盒 `.info`。
- **参数硬编码**: 强制设置为 Ogawa 格式，全局坐标系缩放为 `1.0`，启用 `flatten_hierarchy` 与 `uvs`。
- **可拓展控制**: 如果需要输出动画缓存，可通过传递额外的参数暴露 `start` / `end` 帧配置到 `bpy.ops.wm.alembic_export` 中，目前默认锁定为首帧静态网格。

### 🟡 参数规则 (PARAMETERS)
- `abc_path` (string): 选填 | 任务沙盒 `.info/{source_stem}.abc` | workflow 中必须显式传入。
- `cache_group` (string): 选填 | `cache` | 需要导出的几何体根对象名。workflow 中应从项目配置传入。

### 🟣 输出字段 (OUTPUTS)
- `output_path` (str): 导出的 `.abc` 绝对路径。
