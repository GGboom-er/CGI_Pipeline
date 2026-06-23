---
skill_id: "maya_conform_normals"
name: "统一法线并清零顶点偏移"
dcc: "maya"
tier: "destructive"
pairs_with:
  - "validate_publish"
description: "对 cache/geo 组内所有 mesh 执行 polyNormal conform（normalMode=2, userNormalMode=0, ch=0），统一法线方向并清除顶点局部空间的 pnts 偏移值。"
parameters:
  geo_group:
    type: "string"
    default: ""
    description: "目标组名（默认自动查找 geo/Group/cache）"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "处理后场景"
    - name: "report"
      type: "report"
      label: "法线报告 (.md)"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **不可逆覆盖**: API 直接重写所有选中网格的法线角度信息，不可原路回退。
- **无历史残留**: 强制阻断了 Maya `polyNormal` 产生的冗余形变历史，保持节点干净。

### 🟢 核心逻辑 (CORE LOGIC)
- 定位操作组或在场景根目录泛查 `geo/cache` -> 获取所有不被引用的可见网格 -> 使用统一策略重定向法线 `normalMode=2 (Conform)` -> 剥离构造历史。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.polyNormal(normalMode=2, userNormalMode=0, constructionHistory=False)`。
- **性能优势**: 禁用了 Construction History (`ch=False`)，有效防止了繁重的操作堆栈导致的卡顿。
- **可拓展控制**: 当前模式是 Conform（法线统一直朝外）。若处理由其他 DCC 反向生成的破损植被或毛发片，可能需要额外加入 `UnlockNormals` 和 `SetToFace` 的二次柔化预处理。

### 🟡 参数规则 (PARAMETERS)
- `geo_group` (string): 选填 | 无 | 要执行法线修正的特定层级。若置空，脚本会自动依次探测并降级捕捉常见的命名组如 `geo`、`Group` 或 `cache`。
