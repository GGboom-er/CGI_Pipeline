# CGI Pipeline 当前项目基石

## 一、定位

CGI Pipeline 是一个通过唯一 FastMCP 控制 Maya、Blender、UE 的本地执行服务。它只提供两类能力：可直接执行的 API，以及由 API 组成的 Workflow。

## 二、目录

| 目录 | 当前职责 |
|---|---|
| `src/cgi_pipeline/capabilities/` | 原子能力 manifest 与同目录 handler |
| `src/cgi_pipeline/core/` | receipt、参数校验、队列、Worker、配置和路径保护 |
| `src/cgi_pipeline/hosts/` | Maya、Blender、UE 第一方适配器与前台 bridge |
| `src/cgi_pipeline/server/` | 固定 MCP surface：目录、帮助、执行和 Workflow |
| `src/cgi_pipeline/dashboard/` | 任务监控和节点图 UI |
| `packages/` | 独立工具仓身份、分类、源码位置与宿主入口登记 |
| `integrations/` | 第三方 DCC bridge 和上游来源 |
| `deploy/maya/` | Maya module、安装器和真机 doctor |
| `workflows/` | 只引用 canonical `api_id` 的 JSON 组合流程 |
| `config/` | 系统 manifest 与项目配置 |
| `tests/` | API、契约、队列和代表性流程测试 |

## 三、API 约定

每个 API 同时具备：

- `capability.yaml`：唯一机器契约，声明稳定 `api_id`、专业分类、输入、输出、风险和 handler。
- `tools/build_catalogs.py`：校验 manifest 并生成宿主安全 JSON；运行时不扫描 YAML。
- `adapter.py`：将任务送入 DCC 或纯 Python 实现。

目录显示独立专业维度；说明不复制到第二份参数文档。执行结果统一使用 `cgi_pipeline.contracts` receipt 契约。

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
