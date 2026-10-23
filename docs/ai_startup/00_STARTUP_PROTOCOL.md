# CGI Pipeline 启动协议

这是 CGI Pipeline 当前实现的启动入口。治理规则由平台全局桥接继承 Notes 根 `AGENTS.md`；仓内架构和运行说明读根 `README.md`，需要参数时调用 `api_help(api_id)`，不要扫描目录。

## 固定顺序

1. 确认 Python：`Y:/GGbommer/scripts/.conda_envs/brain/python.exe`。
2. 确认项目配置：`config/{project}_config.json` 存在且能通过 `core.config_loader` 校验。
3. 未知能力才读取 `list_apis` 定位 API；已知且 catalog_version 未变化的契约直接复用。
4. 首次读取目标 `api_help(api_id)`，确认参数、执行模式、输出和副作用；缓存契约后直接调用，版本过期时刷新一次。
5. 单 API 用 `execute_api`；同一 DCC 的字面调用批次用 `execute_batch`；可复用或跨 DCC 多步任务用已登记 Workflow。
6. 同一业务任务保留首次返回的 workspace_id，后续调用共用目录和存活场景；get_task_result 恢复某次 task_id，不重新提交已执行流程。场景接续和能力自主升级以根 README.md 的当前契约为准。
7. 任务结束检查 receipt 与 audit_path；仅在返回工作区时检查独立操作报告、根 REPORT.md 和沙盒输出。普通 UE 编辑器调用及只读 Pipeline 调用默认只留审计，不为报告创建沙盒。

## 当前入口

| 目标 | 入口 |
|---|---|
| 查目录 | `list_apis` |
| 查参数和限制 | `api_help(api_id)` |
| 执行原子动作 | `execute_api` |
| 执行同 DCC 字面批次 | `execute_batch` |
| 执行组合流程 | `pipeline_execute_workflow` |
| 查任务 | Dashboard `/api/tasks/{task_id}` 或 MCP workflow 查询 |

## 验证边界

静态改动先运行 manifest/help、契约测试和 `compileall`；最终统一跑代表性跨 DCC Workflow。除非用户明确要求，不逐个启动 DCC 做重复测试，也不回读 `docs/archive/` 推断当前状态。
