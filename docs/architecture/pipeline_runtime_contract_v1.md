# CGI Pipeline 运行时契约

本文是当前 API/Workflow 运行链的边界说明。单个 API 的参数不在这里重复，调用 `api_help(api_id)` 读取对应 manifest。

## 入口与队列

```text
list_apis → api_help → execute_api
                       └→ pipeline_execute_workflow
MCP/Dashboard/CLI → Redis → cgi_queue → cgi@%h (solo, c=1)
```

所有 DCC 和 Pipeline 任务共用同一队列。Worker 内 Workflow 同步执行 API，不再次提交 Celery 子任务。

## 提交数据

```json
{
  "task_id": "uuid",
  "api_id": "maya.asset.save_scene",
  "project": "ysj",
  "asset_name": "ciweiguai",
  "source_path": "...",
  "parameters": {}
}
```

Workflow 节点使用 canonical `api_id`；执行前由 `api.contract` 校验必填输入、执行模式和项目配置。

## Receipt

每个 API 返回：

```json
{
  "api_id": "...",
  "api_version": "1.0.0",
  "status": "SUCCESS",
  "input": {},
  "output": {},
  "elapsed_sec": 0.0
}
```

失败时补 `error`、`recovery_hint`；Workflow 只从上游 receipt 的 `output` 取机器数据。审计 JSONL 和 `REPORT.md` 是用户验收证据。

## 安全与生命周期

- 写入只能进入任务沙盒，`path_guard` 拦截只读盘、发布源和 UNC 保护路径。
- `save_scene` 是破坏性 API 链的最后一步。
- 前台模式必须显式 `foreground_port`；后台模式由 Worker 打开或复用 DCC。
- 服务由 `cgi_pipeline.core.service_manager` 管理；Worker 常驻，不按任务自停。
