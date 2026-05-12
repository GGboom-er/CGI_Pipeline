---
skill_id: "rename_asset"
name: "标准化资产重命名"
dcc: "maya"
description: "将当前场景按管线命名规范重命名并另存为。格式: {project}_{category}_{asset}_{stage}_{task}_v{version}.ma。不修改场景内容。"
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
  task:
    type: "string"
    default: ""
    description: "子任务名（可选）"
  output_dir:
    type: "string"
    default: ""
    description: "输出目录（可选，默认与源文件同目录）"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "重命名后场景"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **写操作覆盖**: 将直接调用引擎命令更改场景，随之带来一个另存为动作。
- **保护退避**: 当 `output_dir` 指向网络保护区（例如定版资产库）但非发布权限，将被拦截至沙盒。

### 🟢 核心逻辑 (CORE LOGIC)
- 提取当前配置变量（项目名/环节名/等） -> 分析当前文件名结构或扫描目标文件夹历史推算新版本号（如 v005 到 v006） -> 拼接标准命名公式 -> 调用 `cmds.file(rename)` -> 执行另存为动作。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.file(rename=True)`, `cmds.file(save=True)`
- **正则表达式**: 底层自带了一套强大的正则捕获逻辑，能够精准把控 `_v010.ma` 以及各种不规范连字符的名称以转化为 `[project]_[category]_[asset]_[stage]_[task]_v[ver].[ext]` 的硬规范。
- **可拓展控制**: 目前写死了对 `version` 递增。可以增加如 `daily` 等副标签（如 `_v006_daily`）做更精细的版本管理流派。

### 🟡 参数规则 (PARAMETERS)
- `project` (string): 必填 | 无 | 项目代号（如 `ysj`）。
- `category` (string): 必填 | 无 | 资产三级分类（如 `chr`）。
- `asset_name` (string): 必填 | 无 | 资产主体名。
- `stage` (string): 必填 | 无 | 目前环节（如 `rig`）。
- `task` (string): 选填 | 环节基础任务名 | 用于指代如 `rigMaster` 这种细分任务槽。
- `output_dir` (string): 选填 | 同当前目录 | 指定执行另存为操作时的存储盘符。
