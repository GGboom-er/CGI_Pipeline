# CGI Pipeline 当前知识库

本文只记录当前有效事实；历史方案和旧入口不作为运行依据。

## 已确认事实

- Notes 受管 Python 3.11 是唯一运行环境：`Tools/_managed/conda_envs/cgi_pipeline`。
- 生产 MCP surface 只有 `list_apis`、`api_help`、`execute_api`、`list_workflows`、`pipeline_execute_workflow`。
- API manifest 是机器真相源；API help 是参数与调用说明真相源；Notes API catalog 只是一句话目录。
- Redis 只承载 broker/进度；所有任务进入 `cgi_queue`，唯一 Worker 使用 solo 并发 1。
- Maya、Blender、UE 的 DCC 类型只影响 adapter 和 warm pool，不创建独立队列或 Python 环境。
- 前台调用必须显式端口；Maya 推荐 `cmds.commandPort(name=":7009", sourceType="python", echoOutput=True)`。
- Workflow 段间只消费上一步 receipt 的 `output`，不读取 Markdown 作为机器数据。
- 所有产物写任务沙盒；发布源、只读盘和 UNC 保护路径禁止写入。
- `save_scene` 只能作为破坏性 API 链的最后一步。

## 当前主线 Workflow

`tex_to_rig_verify_and_sync`：项目配置解析 → Blender ABC/材质采集 → Maya 层级与几何对比 → 增量同步 → 材质应用 → 验证 → 保存。

其他可复用流程以 `list_workflows` 和 `workflows/*.json` 为准；API 明细以 `list_apis`/`api_help` 为准。

## 故障定位

1. API 不存在：先 `list_apis`，不要猜短名。
2. 参数错误：读取该 API 的 `api_help.md`，检查 `inputs` 和项目配置。
3. 任务未启动：检查 Redis、Worker 心跳和 `cgi_queue`，不要重复启动多个 Worker。
4. DCC 前台失败：列出当前会话并显式传端口。
5. 结果不完整：先读 receipt 和 REPORT.md，再检查任务沙盒中的 `.info` JSON/ABC。

## 验证门禁

改 API 或运行链后运行 `tools/check_pipeline_governance.py`、API 契约测试、`compileall`，最后统一执行代表性 Workflow smoke。
