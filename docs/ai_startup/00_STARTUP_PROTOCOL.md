# CGI Pipeline 启动协议

这是 CGI Pipeline 当前实现的启动入口。治理规则由平台全局桥接继承 Notes 根 `AGENTS.md`；仓内架构和运行说明读根 `README.md`，需要参数时调用 `api_help(api_id)`，不要扫描目录。

## 固定顺序

1. 确认 Python：`Y:/GGbommer/scripts/.conda_envs/brain/python.exe`。
2. 确认项目配置：`config/{project}_config.json` 存在且能通过 `core.config_loader` 校验。
3. 读取 `list_apis` 或运行 `tools/sync_notes_api_index.py --check`，定位当前 API。
4. 调用目标 API 的 `api_help(api_id)`，确认必填参数、执行模式、输出和副作用。
5. 单 API 用 `execute_api`；多步任务用已登记 Workflow。
6. 任务结束检查 receipt、REPORT.md 和任务沙盒输出。

## 当前入口

| 目标 | 入口 |
|---|---|
| 查目录 | `list_apis` |
| 查参数和限制 | `api_help(api_id)` |
| 执行原子动作 | `execute_api` |
| 执行组合流程 | `pipeline_execute_workflow` |
| 查任务 | Dashboard `/api/tasks/{task_id}` 或 MCP workflow 查询 |

## 验证边界

静态改动先运行 manifest/help、契约测试和 `compileall`；最终统一跑代表性跨 DCC Workflow。除非用户明确要求，不逐个启动 DCC 做重复测试，也不回读 `docs/archive/` 推断当前状态。
