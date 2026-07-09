---
mcp_expose: true
skill_id: "validate_publish"
name: "发布前质量门禁"
dcc: "maya"
tier: "read"
pairs_with:
  - "save_scene"
description: "发布前自动化 QC 检查：验证场景完整性、cache 组存在性、mesh 数量、未知节点、空组、命名规范。只读操作，输出 PASS/FAIL 报告。"
parameters:
  project:
    type: "string"
    default: ""
    description: "项目代号，为空则从数据流推断"
  category:
    type: "string"
    default: ""
    description: "资产分类，为空则从数据流推断"
  asset_name:
    type: "string"
    default: ""
    description: "资产名称，为空则从数据流推断"
  stage:
    type: "string"
    default: ""
    description: "制作阶段，为空则从数据流推断"
  cache_group:
    type: "string"
    default: ""
    description: "cache 组名称，为空则自动查找"
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
      label: "QC 报告 (.md)"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **绝对只读**: 分析器纯获取数据状态生成合规打分卡，绝不替用户修改任何一行规范报错，杜绝因机器自动化清理而引起的“资产不知情损毁”。
- **强力阻断阀**: 任何一环判定为 `FAIL` 就会彻底驳回流水线任务入库的下一步指令提交。

### 🟢 核心逻辑 (CORE LOGIC)
- 抓取基础信息预检 -> 核查场景合法性 -> 校验预定 Cache 层级完备性 -> 网格数量清算 -> 侦测是否含有未知废弃第三方插件垃圾 -> 查验死组残留 -> 比对管线强制命令学基准 -> 输出汇总的过关或驳回报表。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.ls(type='unknown')`, `cmds.listRelatives`, 辅以原生 Python `re` 模块深度规则探明。
- **自定义规范校验**: 不仅检查空节点，也查违规命名和非 `Shape` 乱入。
- **可拓展控制**: 各管线的 `publish` 规范要求各异，可随时针对游戏骨骼、蒙皮点数或渲染着色层级（RenderLayer）增加检查条目以实现更高精度的准入壁垒管控。

### 🟡 参数规则 (PARAMETERS)
- `project` (string): 选填 | 无 | 取环境默认变量。
- `category` (string): 选填 | 无 | 取环境默认变量。
- `asset_name` (string): 选填 | 无 | 取环境默认变量。
- `stage` (string): 选填 | 无 | 取环境默认变量。
- `cache_group` (string): 选填 | 自动侦测 | 检查资产是否有指定主输出组。
