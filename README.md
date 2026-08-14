# CGI Pipeline

CGI Pipeline 是通过唯一 FastMCP 后台连接 Maya、Blender、UE 并执行 API/Workflow 的本地管线。

## 调用层级

```text
AI
 └─ FastMCP
    ├─ list_apis / api_help       目录与渐进式说明
    ├─ execute_api                单个确定性原子动作
    ├─ get_task_result            异步任务结果恢复
    └─ list_workflows / pipeline_execute_workflow
                                  多 API 组合与报告
```

API 代码只做一件可复用的小事；Workflow 负责组合、跨 DCC 数据传递和最终报告。MCP 不动态生成第二套 API，也不保留旧 Skill 入口。

## 运行环境

- Python：共享大脑环境 `Y:/GGbommer/scripts/.conda_envs/brain`（Python 3.11），CGI 与大脑及其他本地工具共用。
- Redis：Notes 受管 `Tools/_managed/redis/redis-server.exe`。
- Worker：一个 `cgi_queue`、一个 `cgi@%h`，串行执行并复用 DCC warm pool。
- DCC：Maya 2025+、Blender 5.0+、UE 5.x；路径通过 `.env` 和 `config/{project}_config.json` 配置。

## 快速使用

```powershell
$py = "Y:\GGbommer\scripts\.conda_envs\brain\python.exe"
& $py -m cgi_pipeline.dashboard.app
& $py -m cgi_pipeline.cli list-apis
& $py -m cgi_pipeline.cli api-help blender.asset.blender_export_abc
& $py -m cgi_pipeline.cli run-workflow tex_to_rig_verify_and_sync --project ysj --asset ciweiguai
```

服务管理器会按需拉起 Redis 和 Worker。前台执行必须在参数中显式传 `execution_mode=foreground` 与 `foreground_port`；后台执行默认走单队列。

## API 目录

稳定公开入口：

```python
from cgi_pipeline import api, client, tools
```

`api.execute_local()` 只在 Maya/Blender 等宿主内执行安全能力；`client.execute()` 才加载 FastMCP 并调用远程服务。每个能力目录包含：

```text
src/cgi_pipeline/capabilities/**/capability.yaml  # 唯一机器契约
src/cgi_pipeline/capabilities/**/*.py             # 同目录 handler/实现
src/cgi_pipeline/data/*.json                      # 生成的运行时目录
```

`api_help(api_id)` 返回生成目录中的 manifest 契约；修改 `capability.yaml` 后运行 `tools/build_catalogs.py`，治理检查会拒绝过期 JSON，不保留第二份手写参数文档。

外部工具不复制源码：`packages/registry.yaml` 登记身份、分类、源码位置和宿主入口。Maya 部署只通过 `deploy/maya/modules/CGIPipeline.mod` 暴露 `src`。

目录用途由自动生成的 Notes 目录维护：`Y:/GGbommer/scripts/Notes/ai/projects/cgi_pipeline_api_catalog.md`。同步命令：

```powershell
& $py tools/sync_notes_api_index.py --write
```

新增项目只复制并修改 `config/ysj_config.json`，无需改 API 代码。

## Workflow

当前主线包括 `tex_to_rig_verify_and_sync`、`blender_tex_export`、`blender_to_maya_full_build`、`abc_import_with_materials`、`full_cleanup_and_save` 和 `qc_and_publish`。Workflow JSON 的每个执行节点填写 canonical `api_id`，段间通过 receipt 的 `output` 传值，不能读取 Markdown 作为机器数据。

## 验证

```powershell
& $py tools/check_pipeline_governance.py
& $py tools/build_catalogs.py --check
& $py -m compileall -q src/cgi_pipeline
& $py -m pytest tests -q
```

最终验收再统一跑代表性跨 DCC Workflow，避免逐个启动 DCC 造成无效等待。
