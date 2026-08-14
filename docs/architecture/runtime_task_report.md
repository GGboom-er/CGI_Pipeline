# REPORT.md 运行时报告契约

报告由 `cgi_pipeline.core.task_report_writer` 根据审计 JSONL 和标准 API receipt 生成。Markdown 只供人阅读，不作为后续 API 的输入。

## 报告结构

1. 任务摘要：task_id、项目、资产、Workflow/API 数量和最终状态。
2. 执行步骤：`Step N/Total | api_id | status | elapsed`。
3. Details：每步 receipt 的 summary、output、error 和 recovery_hint。
4. 产物：明确列出任务沙盒中的 JSON、ABC、场景和报告路径。

## 状态

常见终态为 `SUCCESS`、`ERROR`、`TIMEOUT`、`BLOCKED`、`AUDIT_FAILED`、`CHAIN_ABORTED` 和 `CANCELLED`。状态含义以 `config/pipeline_manifest.json` 为准。

## 验收

报告存在且审计条目齐全时，结合输出文件和 receipt 验收 Workflow。不要从旧字段或历史文档推断结果。
