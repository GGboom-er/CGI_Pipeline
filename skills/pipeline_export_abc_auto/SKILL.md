---
skill_id: "pipeline_export_abc_auto"
name: "自动导出 ABC"
dcc: "pipeline"
skip_audit: true
description: "根据源文件类型自动选择 DCC（Maya/Blender）导出 ABC。.blend 走 Blender，.ma/.mb 走 Maya。"
parameters:
  abc_path:
    type: "string"
    default: ""
    description: "输出 ABC 路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。"
io:
  inputs:
    - name: "source"
      type: "any_file"
      label: "源文件 (自适应)"
  outputs:
    - name: "abc"
      type: "abc_file"
      label: "导出的 .abc"
category: "convert"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **子代理委派**: 此技能本体不直接处理文件，而是充当管线的流量中枢。若文件不支持（例如非 ma/mb/blend），将直接返回错误回执抛出异常。
- **后台独占**: 该操作触发后将自动占用后台常驻 Worker，请勿与其余极度消耗 IO 的命令混搭发起以避免竞争。
- **路径规则**: workflow 必须显式传 `abc_path="{{input.info_dir}}/... .abc"`。自动推导也只写任务沙盒 `.info`，不回退源文件或发布目录。

### 🟢 核心逻辑 (CORE LOGIC)
- 抓取请求的文件扩展名 -> 判断引擎阵营 (`.blend` 丢给 Blender Worker，`.ma/.mb` 丢给 Maya Worker) -> 以相应的参数透传调用 `blender_export_abc` 或 `maya_export_abc` -> 抓取对应的回执数据流重新组装。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: 通过引入本地后端的 Celery / MCP 调用架构分发。
- **输出路径推导**: 优先 `parameters.abc_path`，其次 `parameters.info_dir` / `payload.extra_params.info_dir`，最后 `payload.run_dir/.info` 或 `task_id` 对应任务沙盒 `.info`。
- **可拓展控制**: 针对诸如 Houdini 乃至 Unreal 资产互导，可在此直接配置 `.hip` / `.uasset` 新的分流器路由。

### 🟡 参数规则 (PARAMETERS)
- `abc_path` (string): 选填 | 任务沙盒 `.info/{source_stem}.abc` | 透传给子级任务的 Alembic 文件输出路径。

### 🟣 输出字段 (OUTPUTS)
- `output_path` (str): 子级导出技能返回的 `.abc` 绝对路径。
