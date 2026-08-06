# API Help: pipeline.pipeline.write_task_report

- 作用与场景：从 audit JSONL 重建/修复统一 REPORT.md；这是纯 Python pipeline API，不启动 DCC。
- 用法：`execute_api(pipeline.pipeline.write_task_report, params={...})`
- DCC：`pipeline`；层级：`write`；执行：`background`

## 参数
- `task_id`（默认 ``）：任务 ID。为空时从 payload 顶层读。
- `audit_path`（默认 ``）：audit JSONL 路径。为空时自动推导为 audit/{task_id}.json。
- `run_dir`（默认 ``）：沙盒目录绝对路径。为空时从 Redis 读 wf:{task_id}:state.sandbox_dir。
- `mode`（默认 `normal`）：normal / workflow。hold 仅用于旧审计兼容。
- `source_path`（默认 ``）：主源文件路径（仅用于报告头展示）。
- `hold_api_id`（默认 ``）：旧版 hold 模式下被暂停的 api_id。
- `hold_step_idx`（默认 `-1`）：旧版 hold 模式下被暂停的 step 编号。
- `hold_detail`（默认 ``）：旧版 hold 模式下的错误详情。
- `hold_remaining`（默认 ``）：旧版 hold 模式下未执行的剩余步骤 JSON 字符串。

## 输出
- 从 audit JSONL 重建/修复统一 REPORT.md；运行中报告由 core/task_report_writer.py 负责。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
