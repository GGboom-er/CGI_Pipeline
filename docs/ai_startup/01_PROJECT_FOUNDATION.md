# CGI Pipeline 当前项目基石

## 一、定位

CGI Pipeline 是一个通过唯一 FastMCP 控制 Maya、Blender、UE 的本地执行服务。它只提供两类能力：可直接执行的 API，以及由 API 组成的 Workflow。

## 二、目录

| 目录 | 当前职责 |
|---|---|
| `api/` | API manifest、help 和 adapter/handler |
| `core/` | receipt、参数校验、队列、Worker、配置和路径保护 |
| `dccs/` | Maya、Blender、UE 适配器与前台 bridge |
| `workflows/` | 只引用 canonical `api_id` 的 JSON 组合流程 |
| `mcp_server/` | 固定 MCP surface：目录、帮助、执行和 Workflow |
| `dashboard/` | 任务监控和节点图 UI |
| `config/` | 系统 manifest 与项目配置 |
| `tests/` | API、契约、队列和代表性流程测试 |

## 三、API 约定

每个 API 同时具备：

- `api.yaml`：机器契约，声明 `api_id`、输入、输出、风险和 handler。
- `api_help.md`：参数、调用方式、使用场景、限制、失败恢复。
- `adapter.py`：将任务送入 DCC 或纯 Python 实现。

目录只显示 `api_id` 和一句话用途；说明不复制到项目卡或 README。执行结果统一由 `core.receipt.make_receipt` 构造。

## 四、运行链

```text
MCP list_apis/api_help
        ↓
execute_api 或 pipeline_execute_workflow
        ↓
Redis → cgi_queue → 唯一 cgi@%h Worker
        ↓
Maya / Blender / UE adapter → receipt → audit / REPORT.md
```

前台模式必须显式 `foreground_port`；后台模式复用 DCC warm pool。Workflow 在同一 Worker 内顺序消费 API，不再嵌套 Celery 任务。

## 五、项目扩展

新项目只复制 `config/ysj_config.json` 为 `config/{project}_config.json` 并修改路径规则。API、Worker、Python 环境和 MCP 入口保持不变。
