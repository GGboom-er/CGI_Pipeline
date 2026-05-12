---
skill_id: "write_task_report"
name: "任务汇总报告"
dcc: "pipeline"
skip_audit: true
description: "从 audit JSONL 重建/修复统一 REPORT.md。pipeline 类 skill，不开 DCC，主进程内直接执行。运行中报告由 core/task_report_writer.py 负责。"
parameters:
  task_id:
    type: "string"
    default: ""
    description: "任务 ID。为空时从 payload 顶层读。"
  audit_path:
    type: "string"
    default: ""
    description: "audit JSONL 路径。为空时自动推导为 audit/{task_id}.json。"
  run_dir:
    type: "string"
    default: ""
    description: "沙盒目录绝对路径。为空时从 Redis 读 wf:{task_id}:state.sandbox_dir。"
  mode:
    type: "string"
    default: "normal"
    description: "normal / workflow。hold 仅用于旧审计兼容。"
  source_path:
    type: "string"
    default: ""
    description: "主源文件路径（仅用于报告头展示）。"
  hold_skill_id:
    type: "string"
    default: ""
    description: "旧版 hold 模式下被暂停的 skill_id。"
  hold_step_idx:
    type: "number"
    default: -1
    description: "旧版 hold 模式下被暂停的 step 编号。"
  hold_detail:
    type: "string"
    default: ""
    description: "旧版 hold 模式下的错误详情。"
  hold_remaining:
    type: "string"
    default: ""
    description: "旧版 hold 模式下未执行的剩余步骤 JSON 字符串。"
io:
  inputs:
    - name: "audit"
      type: "jsonl_file"
      label: "审计日志"
  outputs:
    - name: "report"
      type: "report"
      label: "任务汇总 .md"
category: "output"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **只读 audit**：只消费 `audit/{task_id}.json`，不执行业务逻辑，不访问 DCC。
- **输出唯一落沙盒**：报告路径强制 `{run_dir}/REPORT.md`，不走全局 `reports/`。
- **非运行时 writer**：本 skill 只用于 audit 重建/灾后恢复；正常任务执行中由 `core/task_report_writer.py` 实时写报告。
- **防递归**：自己不产生 audit（`skip_audit: true`）。
- **后台无暂停**：新任务只渲染完成、失败、拦截和审计失败；不会要求人工选择后继续。

### 🟢 核心逻辑 (CORE LOGIC)
1. 读 audit JSONL，按行 parse 成 entries 列表
2. **normal 模式**：提取所有 `STEP_*` entry 的 `detail`（是 receipt JSON），按 skill 的 `category` + `skill_id` 前缀分派渲染器
3. **workflow 模式**：提取所有 `SEGMENT_*` entry，按 segment 维度渲染多段概览，并展开段内 step 明细
4. 头部总览表 + 主体 + 结尾签名
5. 写入沙盒目录

### 🔵 核心代码与扩展 (IMPLEMENTATION)
- 读取：`_read_audit(path)` 逐行 `json.loads`，容错
- STEP 分派：`_render_step_for(receipt, entry)`，按 `skill_id` 前缀/后缀命中分派表
- SEGMENT 渲染：`_render_segment(rc, seg_idx)`，字段结构 `{dcc, step_count, elapsed_min, summary, outputs.report_path}`
- 扩展新 action 类型：只在 `write_task_report.py` 的 `_DISPATCH` 字典加条目

### 🟡 参数规则 (PARAMETERS)
- `task_id` (string): 选填 | 从 payload 读 | chain 引擎自动注入
- `audit_path` (string): 选填 | `audit/{task_id}.json` | 显式传入用于排障或跨目录汇总
- `run_dir` (string): 选填 | Redis wf:{task_id}:state.sandbox_dir | 外部显式传入
- `mode` (string): 选填 | `normal` | 合法值 `normal` / `workflow`（`hold` 仅旧审计兼容）
  - `normal`：渲染 STEP_* 为主（chain 模式收尾）
  - `workflow`：渲染 SEGMENT_* 为主（workflow 模式收尾），每段链接子 chain md
- `source_path` (string): 选填 | 空 | 展示用
- `hold_*` (多个): 选填 | 空 | 仅用于渲染旧版暂停审计

### 🟣 输出字段 (OUTPUTS)
- `output_path` (str): 沙盒内 `REPORT.md` 的绝对路径
- `result.entries_count` (int): audit 中的总 entry 数
- `result.units_rendered` (int): 渲染到报告里的单位数（normal 模式是 step，workflow 模式是 segment）
- `result.mode` (str): 回显所用模式
- `result.final_status` (str): 从 audit 末尾推断的任务状态
