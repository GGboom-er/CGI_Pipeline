---
skill_id: "maya_compare_mesh_topology"
name: "Mesh 拓扑对比"
dcc: "maya"
tier: "read"
pairs_with:
  - "maya_build_asset_info"
skip_audit: true
description: "无头模式对比参考组和目标组的 mesh 拓扑，报告未匹配项和顶点位置差异。只读操作。"
parameters:
  ref_group:
    type: "string"
    default: ""
    description: "参考组名（通常带命名空间）"
  tgt_group:
    type: "string"
    default: ""
    description: "目标组名（通常不带命名空间）"
  threshold:
    type: "number"
    default: 0.0001
    description: "顶点差异忽略阈值"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "report"
      type: "report"
      label: "对比报告 (.md)"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读输出**: 纯拓扑与空间位置比对验证，绝不修改模型。
- **匹配要求**: 目标组内若存在孤立的未匹对网格（拓扑差异或距离超阈值），将以警告形式输出但不会中断分析。
- **空间同构**: 比对基于世界坐标进行（或提取时的冻结坐标），因此需确保两者具备相同的空间参照物。

### 🟢 核心逻辑 (CORE LOGIC)
- 提取参考组与目标组内的全部多边形 -> 并行计算各对象顶点数、面数 -> 对拓扑数吻合的网格引入 KDTree 空间距离运算 -> 计算两者所有顶点的空间位置最大偏差 (Max Deviation) -> 分流归纳为：完全一致、位移、丢失或新增等状态。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `scipy.spatial.cKDTree` 处理高维点云匹配，配合 `om.MFnMesh.getPoints` 实现无 UI 高速空间配对。
- **算法优势**: 抛弃传统按名称或 DAG 路径进行硬匹配的落后方案，实现了容忍更名或层级重组的鲁棒性比对。
- **可拓展控制**: 如果目标是为了对比发生过平移操作的变种资产，可以通过修改代码加入中心点重置 (Centering) 偏移纠正算法，再进行 KDTree 对比。

### 🟡 参数规则 (PARAMETERS)
- `ref_group` (string): 必填 | 无 | 基准拓扑所属节点（如 `|ref_cache`）。
- `tgt_group` (string): 必填 | 无 | 待测拓扑所属节点（如 `|Group|Geometry|cache`）。
- `tolerance` (float): 选填 | `0.001` | 两点间的最大允许距离容差（厘米），超过此值将被认定为形状遭到篡改。
