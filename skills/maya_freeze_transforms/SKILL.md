---
skill_id: "maya_freeze_transforms"
name: "冻结变换"
dcc: "maya"
tier: "destructive"
pairs_with:
  - "validate_publish"
description: "冻结场景中所有可变换节点的 Translate/Rotate/Scale，并清除构造历史。"
parameters:
  target_nodes:
    type: "array"
    default: []
    description: "指定节点列表，为空则作用于所有 mesh/transform"
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
      label: "冻结报告 (.md)"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **强破坏性操作**: 清理构建历史，原有的挤出/倒角/蒙皮绑定驱动都将永久性失效，无法回滚。
- **骨骼免疫**: 已在底层加入了安全拦截锁，针对 `joint` 类型的传递将会被忽略，防止因误操导致动画朝向（jointOrient）全毁。

### 🟢 核心逻辑 (CORE LOGIC)
- 定位网格元素 -> 构建目标清单 -> 通过核心 API 执行 TRS 清零 (`makeIdentity`) -> 清理由于挤压或拉伸生成的依赖计算堆栈 (`DeleteHistory`) -> 处理所有遗留报错并汇总成功数量。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.makeIdentity(apply=True, t=1, r=1, s=1, n=0, pn=1)`, `cmds.delete(ch=True)`
- **规避脏节点崩溃**: 为了解决某些特定绑定资产 `blendWeighted` 卡死导致 Freeze 崩溃的问题，底层特别捕获了异常拦截。
- **可拓展控制**: 如果部分资产由于有法线编辑需求而需要保留法线方向，可在 `makeIdentity` 中拓展修改 `n=1 (normal)` 来阻止法线的连带重算。

### 🟡 参数规则 (PARAMETERS)
- `target_nodes` (array): 选填 | 无 | 要清理的目标名字清单，缺省时将执行场景全局所有网格。
