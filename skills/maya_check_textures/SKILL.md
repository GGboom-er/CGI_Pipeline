---
skill_id: "maya_check_textures"
name: "贴图检查"
dcc: "maya"
skip_audit: true
description: "扫描 Maya 场景中所有 file 节点，检查贴图是否存在于磁盘（支持 UDIM），按目录分组汇总报告。"
parameters:
  check_exists:
    type: "boolean"
    default: true
    description: "是否检查贴图文件在磁盘上是否存在"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "检查后场景"
    - name: "report"
      type: "report"
      label: "检查报告 (.md)"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读分析**: 不断开、不重连任何材质贴图，纯报表生成。
- **解析受限**: 仅通过原生的 `file` 节点扫描，若使用特殊渲染器私有贴图节点（如 Arnold 的 `aiImage`），则需要扩展抓取规则。

### 🟢 核心逻辑 (CORE LOGIC)
- 抓取所有 `file` 类型节点 -> 提取 `fileTextureName` 属性 -> 提取并兼容处理 `<UDIM>` 标签或直接数值 -> 聚合相同根目录的资源 -> 可选对所有解析后的绝对路径执行 `os.path.isfile` 的物理查验 -> 生成缺失报告。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.ls(type='file')`, `cmds.getAttr(node + '.fileTextureName')`
- **UDIM 解析**: 代码内置了对 `1001` 等 UDIM 命名规则的识别算法，将其折叠为单一条目 (`<UDIM>`)，避免同一套贴图因为多象限而刷屏。
- **可拓展控制**: 如果需要扫描 `aiImage` 或者 `RedshiftNormalMap`，只需修改初始化检索类型的数组 `type='file'`。当前逻辑仅负责诊断，如果后续需要开发“自动修复断链”技能，可以利用此模块输出的字典进行二次开发。

### 🟡 参数规则 (PARAMETERS)
- `check_exists` (bool): 选填 | `true` | 是否调用本地文件系统 API 验证路径真实存在（可能会因网络存储延迟导致分析变慢）。
