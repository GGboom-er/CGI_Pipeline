# Notes Global Bridge

@Y:/GGbommer/scripts/Notes/AGENTS.md

# CGI Pipeline 项目规则

本仓库是 CGI Pipeline 的唯一实现仓。对外只有一个 FastMCP，内部只有 API Catalog、Workflow、单队列 Worker 和三套 DCC 适配器。

## 当前基线

- 平台：Windows 10+；Maya 2025+；Blender 4.x；UE 5.x。
- Python：使用 Notes 通用受管环境 `Y:/GGbommer/scripts/Notes/Tools/_managed/conda_envs/cgi_pipeline/python.exe`。CGI 不创建专属 Python 环境，也不依赖当前 shell 激活环境。
- 服务：Redis + 一个 `cgi_queue` + 一个 `cgi@%h` Celery Worker（`--pool=solo -c 1`）。Maya、Blender、UE、Pipeline 和 Workflow 共用队列，DCC 只选择适配器和 warm pool。
- 项目接入：只新增 `config/{project}_config.json`，API 代码和公共运行时不按项目复制。
- 资产写入：只能落任务沙盒 `projects/{project}/...` 或 `runs/{task_id}`；受保护发布源和只读盘由 `path_guard` 拦截。

## API 使用契约

API 是唯一原子执行入口；每个 API 由一个 `api_id`、`api.yaml`、`api_help.md` 和 handler 组成。

1. `list_apis`：读取目录，一句话了解用途、DCC、分级和执行模式。
2. `api_help(api_id)`：读取完整参数、前置条件、输出、限制和恢复方式。
3. `execute_api(api_id, params=...)`：执行单个 API；Workflow 的节点也只填写 `api_id` 和 `parameters`。
4. `list_workflows` / `pipeline_execute_workflow`：组合多个 API，段间只通过标准 receipt 的 `output` 传递。

API help 是参数和调用方式的唯一说明位置；项目目录只保留 API 一句话用途。不要新增 Skill 目录、Skill 注册表、Skill 兼容入口或第二套参数说明。

### API 结构

```text
api/
├── maya/|blender/|ue/|pipeline/.../<api_id>/
│   ├── api.yaml       # 机器契约：api_id、inputs、outputs、handler、风险等级
│   ├── api_help.md    # 人和 AI 逐步读取的使用说明
│   └── adapter.py     # 适配器入口；复杂实现可由同目录模块承载
└── registry.py        # 只读取 api.yaml，不维护第二份注册表
```

handler 必须返回 `core.receipt.make_receipt` 生成的标准 receipt，至少包含 `api_id`、`api_version`、`status`、`input`、`output`、`elapsed_sec`。错误必须返回可读的 `error` 和 `recovery_hint`，不得静默吞异常。

## 执行模式

| 模式 | 约定 |
|---|---|
| `background` | 交给唯一 `cgi_queue`，Worker 自动打开或复用 DCC 场景，返回 `task_id`/receipt。 |
| `foreground` | 必须显式传 `foreground_port`；未知端口先发现会话，禁止默认猜端口。 |
| Workflow | Worker 内同步执行 API 链，外部提交异步；`save_scene` 只能是破坏性链的最后一步。 |

查看当前已打开的 Maya/Blender/UE 场景时，使用对应 API 的 `foreground` 模式。Maya 建议：

```python
cmds.commandPort(name=":7009", sourceType="python", echoOutput=True)
```

## 常用命令

```powershell
$py = "Y:\GGbommer\scripts\Notes\Tools\_managed\conda_envs\cgi_pipeline\python.exe"
& $py cli.py list-apis
& $py cli.py api-help maya.rig.reference.update
& $py cli.py run-api maya.rig.reference.update --project ysj --param asset_name=ciweiguai
& $py cli.py list-workflows
& $py cli.py run-workflow tex_to_rig_verify_and_sync --project ysj --asset ciweiguai
& $py tools/check_pipeline_governance.py
```

测试优先运行与本轮改动相关的 `tests/test_api_*.py`、`tests/test_*contract*.py` 和 `compileall`；最终再运行代表性 Workflow smoke。不要逐个启动 DCC 做重复验收。

## 三套 DCC 适配器

- Maya：`dccs/maya/adapter.py` + foreground bridge。
- Blender：`dccs/blender/adapter.py` + `extensions/` bridge。
- UE：`dccs/ue/adapter.py` + `extensions/` WebSocket bridge。

第三方插件只放 `extensions/` 并以同目录 `UPSTREAM.md` 记录来源；不在 API 层复制插件逻辑。

## 禁止事项

- 不恢复旧 `skills/`、`execute_skill`、`list-skills` 或动态 Skill 注册表。
- 不在 API 外再建同一原子动作的入口；不把参数说明复制到 README、项目卡或前端硬编码。
- 不为新项目复制 API 代码或 Python 环境。
- 不在生产代码中直接调用 Celery 子任务嵌套投递；Workflow 在同一 Worker 内同步消费 API。
- 不对受保护源文件原地写入，不用 `subprocess`/`os.system` 绕过 `path_guard`。
