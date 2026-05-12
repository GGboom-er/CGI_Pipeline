---
skill_id: "maya_build_asset_info"
name: "Maya 资产信息采集"
dcc: "maya"
skip_audit: true
description: "遍历指定 Maya 几何根组下所有 mesh，从 Maya 图关系求出的原始几何采集拓扑指纹（顶点数+世界空间坐标），输出标准 _info.json。只负责几何信息，不采集贴图或面级材质。"
parameters:
  info_path:
    type: "string"
    default: ""
    description: "_info.json 输出完整路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。"
  cache_group:
    type: "string"
    description: "必填。要采集的 Maya 几何根组，由项目配置或 workflow 传入。"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "info"
      type: "json_file"
      label: "_info.json"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读输出**: 纯数据采集，无场景写入动作。
- **职责边界**: 本技能只生成几何 `_info.json`。贴图、透明度、UDIM、面级材质分配和 ShapeOrig 清理由其他技能负责。
- **路径规则**: workflow 必须显式传 `info_path="{{input.info_dir}}/..._info.json"`。单技能兜底也只写任务沙盒 `.info`，不回退源文件同目录。
- **保护拦截**: 若指定了 `info_path` 且其属于 `path_guard` 的网络只读区，返回 `BLOCKED`。
- **采集范围**: 先列出 `cache_group` 全层级子孙里自身带 mesh shape 子物体的 transform，再在每个 transform 下检查唯一非 intermediate shape 和可求出的 Orig；transform 可见性不作为过滤条件。
- **采集根组**: `cache_group` 必须由项目配置或 workflow 传入，不在技能内部猜测默认组。
- **名称规则**: `meshes` 的 key 使用当前非 intermediate shape 的 Maya 绝对 DAG 路径；即使几何来自 Orig，也不使用 Orig 名称作为 key。
- **结构规则**: 每个 mesh transform 下必须有且只有一个非 intermediate mesh shape；Shape/Orig 名称不参与采集判断。
- **Orig 规则**: 每个 mesh 只从 Maya 图关系求出的唯一 Orig 采集几何；找不到唯一 Orig 时保留 shape 条目并输出空几何。
- **不做诊断**: 不返回 ShapeOrig 状态、判断详情或修复建议；后续对比/报告节点根据空几何暴露问题。

### 🟢 核心逻辑 (CORE LOGIC)
- 定位 `cache_group` 候选根 → 枚举全层级中自身带 mesh shape 子物体的 transform → 在每个 transform 下验证非 intermediate shape 唯一 → 以 shape 的绝对 DAG 路径作为 JSON key → 优先用 `cmds.deformableShape(shape, originalGeometry=True)` 返回的 plug 列表解析 Orig → 其次从 `shape.tweakLocation -> tweak.input[0].inputGeometry` 反查输入几何 plug → 最后兜底查找同 transform 下唯一 `intermediateObject=True` 且 `outMesh/worldMesh` 有下游连接的 mesh → 找到则采 Orig 顶点，找不到则写空几何 → 汇编 asset_info dict。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **公共采集器**: `dccs.maya.asset_info_collector.collect_scene_info()`；`maya_compare_asset_in_scene` 和 `maya_sync_rig_incremental` 也复用同一套采集逻辑。
- **底层驱动**: `om.MSelectionList`, `om.MFnMesh.getPoints(om.MSpace.kWorld)`
- **输出路径推导**: 优先 `parameters.info_path`，其次 `parameters.info_dir` / `payload.extra_params.info_dir`，最后 `payload.run_dir/.info` 或 `task_id` 对应任务沙盒 `.info`。
- **数据精度**: 坐标通过 `round(x, 4)` 严格约束为 4 位小数（0.0001cm 容差对齐）。
- **为什么用 Orig**: 绑定文件里的当前 shape 可能被修型/绑定变形污染，图关系求出的原始几何保留"绑定师拿到的那份原始几何"。这是对比/拼装的唯一合法基准。
- **有效 Orig 判据**: 不按名称判断。先信任 Maya `deformableShape(..., originalGeometry=True)` 返回的唯一 mesh plug；其次信任 tweak 输入几何连接；兜底才接受同 transform 下唯一 `intermediateObject=True` 且 `outMesh/worldMesh` 有下游连接的 mesh。
- **失败行为**: 不满足唯一非 intermediate shape 或找不到唯一 Orig 时，当前 mesh 输出 `vertices: 0` 与 `vert_positions: []`。
- **JSON 兼容**: 顶层仍保留空 `textures: {}`，用于兼容 `asset_info_schema`；本技能不向其中填充贴图数据。

### 🟡 参数规则 (PARAMETERS)
- `info_path` (string): 选填 | 任务沙盒 `.info/{source_stem}_info.json` | workflow 中必须显式传入。
- `cache_group` (string): 必填 | 无 | 采集根组名、DAG 路径或分号分隔候选，例如 `|Group|Geometry|cache;|*|geo`，由项目配置传入。

### 🟣 输出字段 (OUTPUTS)
receipt.outputs:
- `output_path` (str): `_info.json` 绝对路径。

`_info.json`:
- `source_file`: 当前 Maya 文件路径。
- `meshes`: `{shape_absolute_dag_path: {vertices, vert_positions}}`。
- `textures`: `{}`，仅为 schema 兼容保留。

未采到 Orig 的 mesh:
- `vertices`: `0`
- `vert_positions`: `[]`
