# CGI Pipeline 当前文档索引

| 文档 | 用途 |
|---|---|
| `../../README.md` | 仓内架构、运行入口和开发验证；治理规则由 Notes 全局桥接提供 |
| `00_STARTUP_PROTOCOL.md` | 会话启动与验收顺序 |
| `01_PROJECT_FOUNDATION.md` | 当前目录和架构骨架 |
| `02_KNOWLEDGE_BASE.md` | 当前稳定事实、主 Workflow、排障 |
| `docs/architecture/pipeline_runtime_contract_v1.md` | 运行时 receipt、队列和沙盒契约 |
| `docs/architecture/runtime_task_report.md` | REPORT.md 渲染契约 |
| `src/cgi_pipeline/capabilities/**/capability.yaml`（经 `api_help(api_id)`） | 单个 API 的完整参数和使用说明 |
| `packages/registry.yaml` | 独立工具仓登记与宿主入口 |
| `deploy/maya/` | Maya module、安装器与真机 doctor |
| `../workflows/*.json` | API 组合流程定义 |

参数和用途不在本文重复；目录查 `list_apis`，细节查 API help。`docs/archive/` 只用于明确的历史追溯。
