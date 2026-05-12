---
skill_id: "pipeline_compare_asset"
name: "资产对比（统一入口）"
dcc: "pipeline"
description: "纯 JSON/ABC 数据对比。三步漏斗（路径→等点数→空间）配对，输出 source 侧每个 mesh 相对 target 的 4 种事实归属 + 算法层 7 标签细分。"
parameters:
  input_source:
    type: "string"
    default: ""
    description: "source 侧 _info.json 或 .abc 路径"
  input_target:
    type: "string"
    default: ""
    description: "target 侧 _info.json 或 .abc 路径"
  label_source:
    type: "string"
    default: ""
    description: "source 标签（自动推断: rig/tex/uv/model/anim 等）"
  label_target:
    type: "string"
    default: ""
    description: "target 标签"
  output_path:
    type: "string"
    default: ""
    description: "compare_result.json 输出路径。workflow/单技能调用必须传入任务沙盒 .info 路径或可推导 info_dir。"
io:
  inputs:
    - name: "input_source"
      type: "any_file"
      label: "source _info.json / .abc"
    - name: "input_target"
      type: "any_file"
      label: "target _info.json / .abc"
  outputs:
    - name: "compare_result"
      type: "json_file"
      label: "对比结果 JSON"
    - name: "report"
      type: "report"
      label: "对比报告 (.md)"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读场景**: 仅读 `_info.json` 或 `.abc`，不打开也不修改任何 DCC 场景；会写出标准 `compare_result.json` 到 `output_path`。
- **对比方向**: `input_source` 是参考方（新资产），`input_target` 是被对比方（现有绑定）。所有输出按此方向描述。
- **路径规则**: workflow/单技能调用必须显式传 `output_path` 或 `info_dir`，结果必须位于任务沙盒 `.info`；无法确定 `.info` 输出路径时返回 `ERROR`，不回退输入文件同目录。
- **职责边界**: 本 skill 只做独立对比与结果输出；拼装类 skill 应复用同一套 `core.asset_info_schema.compare()` 内存结果继续执行，不依赖本 skill 输出驱动拼装。
- **向后兼容**: 旧键名 `input_a / input_b / label_a / label_b` 仍被接受，但新 workflow/调用一律用 `*_source / *_target`。

### 🟢 核心逻辑 (CORE LOGIC)
- 加载两份 `_info.json` 或 ABC → 三步漏斗：S1 规范化路径匹配、S2 严格等点数候选竞争、S3 空间深度分析（KDTree + 偏差）→ 每条配对打出算法层标签（IDENTICAL / ORIG_INJECT / MODIFIED / MERGE / SPLIT），未配对的 source 侧记 NEW、target 侧记 DELETE → 聚合成 4 种事实去向 → 输出 MD 内容 + `compare_result.json`。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **算法层**: `core/asset_info_schema.py::compare()`。7 个 actionability 标签保留所有拓扑关系（MERGE/SPLIT 给报告和后续拼装逻辑使用）。
- **输出路径推导**: 优先 `parameters.output_path`，其次 `parameters.info_dir` / `payload.extra_params.info_dir`，最后 `payload.run_dir/.info` 或 `task_id` 对应任务沙盒 `.info`。
- **输入信任**: JSON 只做结构校验；空几何（`vertices=0` 且 `vert_positions=[]`）作为合法事实参与对比，不在本 skill 额外诊断或修复。
- **用户视角**: 4 种去向按以下规则聚合：
  - `identical`         ← IDENTICAL + ORIG_INJECT
  - `matched_different` ← MODIFIED + MERGE + SPLIT
  - `only_source`       ← 未配对的 source 侧 mesh
  - `only_target`       ← 未配对的 target 侧 mesh
- **判定阈值**: `precision_exact` / `precision_loose` 由 `rig_sync_profile`（项目配置）驱动，默认 `0.0001cm / 0.005cm`。
- **扩展**: CPD 全局预对齐默认关闭（大场景易爆内存），仅在小对级别场景可打开。

### 🟡 参数规则 (PARAMETERS)
- `input_source` (string): 必填 | 无 | source 侧 `_info.json` 或 `.abc` 路径。
- `input_target` (string): 必填 | 无 | target 侧 `_info.json` 或 `.abc` 路径。
- `label_source` (string): 选填 | 自动推断 | source 标签（从路径中的阶段名推出：rig/tex/uv/model/anim/fx 等）。
- `label_target` (string): 选填 | 自动推断 | target 标签。
- `output_path` (string): 选填 | 任务沙盒 `.info/{source_stem}_vs_{target_stem}_compare_result.json` | workflow/单技能调用必须显式传入，或由框架提供 `info_dir/run_dir` 推导。

### 🟣 输出字段 (OUTPUTS)
receipt.outputs:
- `output_path` (str): 标准 `compare_result.json` 路径。

`compare_result.json` 结构：
- `schema_version`: 当前为 `compare_result.v1`
- `inputs`: source/target 输入路径与标签
- `compare`: `core.asset_info_schema.compare()` 完整返回，包含 `pairing_groups` 和 `target_only_dags`

receipt.summary 记录差异总数；receipt.report_content 是 MD 格式对比报告，由统一任务报告插入最终 Markdown。
