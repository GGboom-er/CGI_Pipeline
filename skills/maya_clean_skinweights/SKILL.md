---
mcp_expose: true
skill_id: "maya_clean_skinweights"
name: "清理蒙皮权重"
dcc: "maya"
tier: "destructive"
pairs_with:
  - "validate_publish"
description: "移除蒙皮权重中的微量噪声（低于阈值），规范化权重总和为 1.0。需要提供包含蒙皮网格的源场景文件。"
parameters:
  threshold:
    type: "number"
    default: 0.001
    description: "噪声阈值，低于此值的权重将被清零"
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
      label: "清理报告 (.md)"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **不可逆覆盖**: API 层面直接重写内存中的顶点绑定权重数组，由于采用高性能 API，此操作不提供 Maya Undo 堆栈支持，建议事先保存或开启自动备份。
- **作用域**: 默认清扫当前开启场景内检测到的全部 `skinCluster`。

### 🟢 核心逻辑 (CORE LOGIC)
- 提取全量 skinCluster -> 为每个簇抓取 `influence` 数量及顶点集 -> 读取权重并识别小于容差的干扰项 -> 归零噪音数据 -> 计算剩余有效权重并按比例除以新总和 (Normalize=1.0) -> 回写至引擎。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `om.MFnSkinCluster.getWeights()`, `om.MFnSkinCluster.setWeights()`
- **性能优势**: 相较于 `cmds.skinPercent` 的数秒级逐顶点遍历，改用 `OpenMaya 2.0` 的双精度数组 (`MDoubleArray`) 批处理，实现了万面级网格的毫秒级清理。
- **可拓展控制**: 对于特定的骨骼绑定或对极致平滑有追求的面部模块，若允许小数点后四位的微量权重，可以在调用时覆盖 `threshold` 降低过滤等级。

### 🟡 参数规则 (PARAMETERS)
- `threshold` (number): 选填 | `0.001` | 定义被视为“噪音”的权重阈值上限，低于此值的权重将被彻底清零。
