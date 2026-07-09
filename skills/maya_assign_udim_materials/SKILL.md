---
mcp_expose: true
skill_id: "maya_assign_udim_materials"
name: "UDIM 材质分配"
dcc: "maya"
tier: "write"
pairs_with:
  - "blender_extract_materials"
  - "maya_apply_materials"
description: "根据 ABC FaceSet + 精简贴图映射 + Maya UV 分析，为 mesh 按 UDIM 象限创建材质球、连接贴图并按面赋予。"
parameters:
  texmap_path:
    type: "string"
    default: ""
    description: "精简贴图映射 JSON（{材质名: 贴图路径}），优先使用"
  manifest_path:
    type: "string"
    default: ""
    description: "旧版完整材质清单 JSON（兼容回退）"
  srcimg_root:
    type: "string"
    default: ""
    description: "贴图源目录根路径（如 X:/Project/ysj/sourceimages）"
  category:
    type: "string"
    default: ""
    description: "资产类别（如 chr/prp），为空则从数据流推断"
  stage:
    type: "string"
    default: ""
    description: "制作阶段（如 tex），为空则从数据流推断"
  tex_output_dir:
    type: "string"
    default: ""
    description: "贴图拷贝目标目录，为空则不拷贝"
  target_group:
    type: "string"
    default: ""
    description: "限定处理范围的组名，为空则处理全场景"
  asset_name:
    type: "string"
    default: ""
    description: "资产名称，用于匹配 sourceimages 目录"
  info_path:
    type: "string"
    default: ""
    description: "任务 .info 产物目录路径"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
    - name: "manifest"
      type: "json_file"
      label: "材质清单 (.json)"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "已赋材场景"
category: "material"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **场景修改**: 会大范围创建 Lambert 材质球、ShadingGroup (SG)、File 节点及 Place2dTexture，并打断面级别的默认赋予。
- **保护拦截**: 若指定了贴图拷贝输出目录 (`tex_output_dir`)，系统将校验其不位于 `path_guard` 保护的网络生产大盘内，否则拒绝拷贝。

### 🟢 核心逻辑 (CORE LOGIC)
- 提取目标所有 Mesh -> 利用 OpenMaya 提取各个面上顶点的 UV 质心以判定 UDIM 象限 (如 1001) -> 将同一 UDIM 的面收集到一起 -> 根据信息清单查找到对应贴图 -> 创建 Lambert+SG+File -> 仅对该组面进行分配 (`cmds.sets`)。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `om.MItMeshPolygon`, `cmds.shadingNode`, `cmds.sets(forceElement=True)`。
- **匹配策略**: 优先依赖导入时的 FaceSet(SG)，其次通过名称包含关系模糊匹配 JSON 里的键值对，最后降级到目录直接扫描寻找 `<UDIM>` 文件。
- **可拓展控制**: 默认创建 `lambert` 材质，如需对接 Arnold 或 Redshift，可在核心代码中替换 `cmds.shadingNode('aiStandardSurface')`；目前提取的贴图局限于 BaseColor，若扩展可遍历 `Normal/Metal` 字典创建对应 shading node 网络。

### 🟡 参数规则 (PARAMETERS)
- `info_path` (string): 必填 | 无 | 指向 `_info.json` 或 `_texmap.json` 的路径，用于提取贴图列表。
- `cache_group` (string): 选填 | `cache` | 需要执行材质赋予的父组节点。
- `tex_output_dir` (string): 选填 | 无 | 若存在，则会将网络贴图拷贝至此目录并在 Maya 中采用相对路径连接。
