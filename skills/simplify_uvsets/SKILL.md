---
skill_id: "simplify_uvsets"
name: "UV Set 精简"
dcc: "maya"
tier: "destructive"
pairs_with:
  - "check_uvsets"
description: "清理绑定体多余 UV set。对 cache 组下每个 mesh 执行沙盒提取 → UV 清理 → 数据回灌，确保最终只保留一个名为 map1 的 UV set。支持绑定体（通过 ShapeOrig 操作）。破坏性操作，已包裹 undo 块。建议先用 check_uvsets 查看再执行。"
parameters:
  cache_group:
    type: "string"
    default: "cache"
    description: "操作的根组名称，默认 cache"
io:
  inputs:
  outputs:
    - name: "result"
      type: "json"
      label: "UV 精简结构化结果"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **非破坏性沙盒**: 底层运用独家沙盒（克隆至临时 Mesh 节点并断联）模式处理，不改变原蒙皮权重与其他依赖，处理完毕后自动拔除沙盒。
- **只读屏蔽锁**: 内置引用资产（Reference）及锁定节点锁（LockNode）侦测器，一旦遭遇硬性写保护，会直接跳过该个体的强清理以免触发系统奔溃。
- **撤销支持**: 全链条包裹于单独的 Undo 块内。

### 🟢 核心逻辑 (CORE LOGIC)
- 定位 cache 层级 -> 找寻所有目标 Mesh，排除受阻节点 -> 创建独立临时沙盒 Mesh 连入 OutMesh -> 判定并保留最佳包含可用数据值的 UV集 -> 删去其余废弃集 -> 重命名至全局标准名 `map1` -> 通过 InMesh 灌回原始 `ShapeOrig` -> 同步引擎 UI 和幽灵标签。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.polyUVSet`, 通过 Node `inMesh`/`outMesh` 连线传递数据以规避拓扑破坏。
- **最佳 UV 推理**: 具备回退推演功能。如果首集无内容而子集存在内容，能自行完成主次换位拷贝 `polyCopyUV` 并转正首发集地位。
- **可拓展控制**: 对于部分采用多 UV 作为特殊法线渲染的项目（二套 UV 做污迹图），可通过传参修改逻辑块保留特定的白名单 `uvSetName`，而非绝对性的全部清剿。

### 🟡 参数规则 (PARAMETERS)
- `cache_group` (string): 选填 | `cache` | 需要执行化繁为简操作的目标对象组群名。

### 🟣 标准执行记录 (RECORD)
- `output.result.cleaned` (int): 完成重度清理的 mesh 数。
- `output.result.fast_passed` (int): 已符合规范并快速通过的 mesh 数。
- `output.result.skipped` (int): 因无 shape、引用或锁定跳过的 mesh 数。
- `output.result.errors` (list): 单 mesh 清理失败清单。
