---
skill_id: "maya_split_udim_materials"
name: "UDIM 材质自动拆分"
dcc: "maya"
description: "根据 geo 组内 mesh 的 UV 象限自动拆分材质球并重连贴图。支持 UDIM 格式贴图的自动匹配与按面赋予。"
parameters:
  tex_root:
    type: "string"
    default: ""
    description: "贴图根目录路径，用于自动匹配 UDIM 贴图文件"
  prefix:
    type: "string"
    default: ""
    description: "贴图文件名前缀过滤（如角色名）"
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
      label: "拆分报告 (.md)"
category: "material"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **不可逆覆盖**: 会拆毁原始材质的网络赋予链路（但不删除原始材质本体），重连并新生出数倍于原数量的按象限分配的新材质和 SG 节点。
- **特定范围**: 只对那些连接了含 `<UDIM>` 标签或 1001 序列贴图的 File 节点进行破拆计算。

### 🟢 核心逻辑 (CORE LOGIC)
- 抓取待处理多边形面 -> 推导其当前指派的 ShadingGroup -> 反查是否存在符合 UDIM 规范的颜色贴图 -> 根据 `polyEvaluate` 取出各面的中心 UV 点并计算所属象限 -> 克隆源材质、构建新 SG -> 将对应区块的多边形面剥离旧 SG 并分配给新 SG。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.duplicate(upstreamNodes=True)`, `cmds.polyEvaluate`, `cmds.sets(forceElement=True)`。
- **链路保留**: 利用 `upstreamNodes=True` 实现克隆时，不仅复制核心材质，更连带复制 Bump/Roughness 等完整的上游节点拓扑。
- **可拓展控制**: 目前通过分析面的边界框中心（Bounding Box Center）来划分 UV，若是面横跨数个 UDIM 象限会导致错误归类。高级场景可考虑替换为基于 OpenMaya 顶点级别的严格剪裁算法。

### 🟡 参数规则 (PARAMETERS)
- `cache_group` (string): 选填 | `cache` | 需要执行裂解多象限材质算法的层级父组名字。
