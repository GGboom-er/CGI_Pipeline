---
skill_id: "maya_export_abc"
name: "Maya 导出 ABC"
dcc: "maya"
tier: "write"
pairs_with:
  - "maya_import_abc"
  - "pipeline_compare_asset"
description: "将当前 Maya 场景导出为 Alembic (.abc)，含 UV、FaceSet、可见性，Ogawa 格式。不保留 skinCluster。"
parameters:
  abc_path:
    type: "string"
    default: ""
    description: "ABC 输出完整路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。"
  frame_range:
    type: "array"
    default:
      - 1
      - 1
    description: "导出帧范围 [start, end]"
  root_nodes:
    type: "string"
    default: ""
    description: "要导出的根节点列表。为空则导出全部顶层。"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "abc"
      type: "abc_file"
      label: "导出的 .abc"
category: "convert"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读分析**: 导出进程中坚决锁定原始层级，不引入历史节点污染。
- **脱绑提取**: 缓存格式为 Alembic，原生不支持记录和还原 `skinCluster` 或者驱动骨骼关系，仅用于纯净的几何动画 / 静态互导。
- **路径规则**: workflow 必须显式传 `abc_path="{{input.info_dir}}/... .abc"`。单技能兜底也只写任务沙盒 `.info`，不回退源文件同目录。

### 🟢 核心逻辑 (CORE LOGIC)
- 抓取预定目标根节点列表 -> 拼接组装 AbcExport 渲染参数 -> 后台执行 AbcExport CLI 作业。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: `cmds.AbcExport(j=job_command)`。
- **输出路径推导**: 优先 `parameters.abc_path`，其次 `parameters.info_dir` / `payload.extra_params.info_dir`，最后 `payload.run_dir/.info` 或 `task_id` 对应任务沙盒 `.info`。
- **参数硬编码**: `dataFormat=ogawa`, `uvWrite`, `writeFaceSets`, `writeVisibility`, `writeUVSets` 均为默认启用，以满足最高级别的资产拓扑迁移标准。
- **可拓展控制**: 目前仅暴露了根节点的设置（可复数添加 `-root` 参数）。若以后面临诸如体积粒子或曲线导出，可根据节点类型判断增加额外 job flag。

### 🟡 参数规则 (PARAMETERS)
- `abc_path` (string): 选填 | 任务沙盒 `.info/{source_stem}.abc` | workflow 中必须显式传入。
- `root_nodes` (string): 选填 | 场景级顶层 | 使用半角逗号分隔的，想要独立导出的指定节点 DAG 名。
- `frame_start` (int): 选填 | `1` | 导出起止范围帧，缺省视同为静态单帧。
- `frame_end` (int): 选填 | `1` | 导出起止范围帧。


**框架注入参数**（由 workflow/chain 框架自动注入，用户不需要手动传入）：
- `asset_name`、`project`、`run_dir`、`task_id`、`info_dir`、`extra_params`、`submitted_at` 等由调度框架根据当前任务上下文自动填充。
### 🟣 输出字段 (OUTPUTS)
- `output_path` (str): 导出的 `.abc` 绝对路径。
