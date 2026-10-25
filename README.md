# CGI Pipeline

CGI Pipeline 通过 FastMCP 入口连接 Maya、Blender、UE 并执行 API/Workflow。后台工作区自动亲和独立运行通道，默认最多三个工作区并行，同工作区串行；资源不足会在派发前明确拒绝。

## 调用层级

```text
AI
 └─ FastMCP
    ├─ list_apis / api_help       目录与渐进式说明
    ├─ execute_api                单个确定性原子动作
    ├─ execute_batch              同一 DCC 的字面 API 批次
    ├─ get_task_result            异步任务结果恢复
    └─ list_workflows / pipeline_execute_workflow
                                  多 API 组合与报告
```

API 代码只做一件可复用的小事；`execute_batch` 负责同一 DCC、无需模板的多次原子调用；Workflow 负责复用模板、跨 DCC 数据传递和最终报告。MCP 不动态生成第二套 API，也不保留旧 Skill 入口。

CLI 的 `run-api` / `run-chain` 和 Dashboard 节点执行复用服务提交 owner，统一参数校验、工作区绑定和队列提交；CLI 不再单独创建并关闭 DCC Worker。CLI 三类执行命令均可传 `--business-task-id` / `--workspace-id`。

### 当前维护边界

`pipeline.workspace.relocate` 是既有稳定工作区变更存储位置的唯一入口。它由另一个已绑定的稳定维护任务调用，先 `mode=preview` 验证批准的逐成员 SHA 清单和完整实时活动，再 `mode=apply` 提交同一清单摘要。业务 `project` 与存储命名空间独立；新绑定的 `project` 表示原生存储身份，`business_project` 保留业务配置。此能力不改变自动分配策略，不改 DCC 文件内容或历史 audit/state/result/receipt 原文。旧任务恢复返回有效位置及 `original_paths`，原 receipt 内的历史路径仍保持原值；`effective_paths`、`effective_artifacts` 和 `effective_outputs` 明确提供当前位置及存在性。根 `REPORT.md` 属于可重建当前视图，其原文先按 SHA 备份在维护 `.info/{task_id}_original_REPORT.md`，历史操作报告仍保持原字节，新根报告的导航由原 renderer 投影到新地址。迁后恢复场景须显式指定已保存的新实际路径打开；旧 runtime generation 不会自动续接。

源/目标工作区 fence 防止当前版本提交与迁移并发；全量摘要和前台检查不持全局 allocation 锁，仅 rename 与提交发布短暂持锁。部署后必须确认提交端已加载当前实现；未更新的任意旧客户端不受新 fence 保护。实时检查覆盖 broker、runtime 归属，以及已登记前台 Maya/Blender 会话与本机进程；前台场景或完整引用列表指向源/目标都会拒绝，无法完整查明也拒绝，不保存或关闭任何会话。贴图、缓存及其他 DCC 依赖仍需对采用场景另外验证。已有目标默认拒绝；明确 `preserve_members` 只允许顶层无交集，目标每个原成员保持原相对路径，源成员逐项无覆盖 rename，不执行通用递归合并。再次迁移已经迁过的工作区、或迁移持有其他迁移证明的维护根均拒绝，避免形成无支持的恢复链。

每次 apply 在维护工作区平铺保存 `.info/{task_id}_relocation.json` 和 `_relocation_journal.json`；目标 `.info/{task_id}_relocation_commit.json` 是无覆盖原子发布的提交见证。客户端超时先用原 `get_task_result(task_id)` 查询。已提交即使丢回复也恢复原成功；进程在提交前退出则返回 `WORKSPACE_RELOCATION_COMMIT_UNKNOWN`，不得重交 apply。此时从原维护工作区调用同一 API 的 `mode=reconcile, original_task_id=<原ID>`，源/目标身份保持原值，附上新封存的源和目标活动证据；原批准清单从原始 native result 读取。它先核对全部原成员及完整原/本次预期身份，确认后仅回滚未提交事务，不重放迁移；双方出现任何额外修改则保留原位并拒绝。回滚后原 ID 返回明确 `BLOCKED/rolled_back` 并保留持久回滚见证；只有验证该见证后，新批准的操作 ID 才可接替位置路由。已提交但派生报告未完成时，纯查询返回 `report_view_status=PENDING`，显式 reconcile 仅补建该报告并恢复成功，不重放迁移。参数定义和模式以 `api_help` 的机器契约为准。

- 公开恢复能力是 `get_task_result(task_id)` 取回原任务结果，以及工作区 `scene_action=continue/open` 接续或恢复场景；不提供失败 Workflow 从断点继续执行。内部 resume 仅保存段标记和 outputs（Redis 24 小时），不证明场景、产物或未完成段可安全重放。当前无明确生产续跑需求，暂不扩展公开参数；未来需要时须验证版本/输入一致、产物有效、场景恢复及未完成段幂等，再复用现有 owner。
- Dashboard 的注册预设使用服务端编译、参数选择和正式 Workflow 提交；手工字面图复用同一 DCC 的 batch 提交。前端导入、启动和关闭不拥有共享 Worker 的启停权。注册预设执行由统一提交入口按需准备服务，手工图保留执行前就绪检查。
- 托管工作区（`workspace_mode`）不参与旧沙盒 TTL 自动删除。Notes 任务归档、操作成功或目录时间较旧，都不自动等于文件可删；后续只生成待确认候选，核对任务归属、未完成操作、驻留未保存场景及产物保留位置，再取得对象级删除授权。缺少归属或状态记录时保留并标未知。
- 测试必须使用临时目录或既有 `test_workspace.root` 夹具隔离工作区和报告；仅替换队列发送不足以隔离提交副作用。测试不应向真实 `projects/` 写入。

旧工作区有文件而缺原生身份时，独立维护任务通过单独的 `execute_api(pipeline.workspace.adopt)` 控制入口接管。外层仍绑定已有效的维护 `project/business_task_id/workspace_id`；内层仅接受目标 `target_project/business_task_id`、维护目录内的 `manifest_relpath/manifest_sha256`、`activity_relpath/activity_sha256` 六参数。目标固定派生为 `projects/{target_project}/{business_task_id}`，不接受任意目录或 `approved=true`。Notes 的独立批准事实绑定实际六元组；CGI 服务自行重验数据和活动，不能以参数自证授权。该控制 API 不能放进 batch 或 Workflow。

`workspace_adoption.validate_adoption_evidence` 是 Hook 和服务共用的纯读 owner。manifest schema 1（`cgi_workspace_adoption_manifest`）封存目标身份、按 POSIX 相对路径排序的全部目录/文件及文件大小和 SHA256、`file_count/total_bytes`。验证拒绝链接/reparse、别名、遗漏、重复和变化；哈希后统一复核所有文件/目录身份，不缓存结果。activity schema 1（`cgi_workspace_adoption_activity`）带最长 300 秒的 `captured_at_epoch/expires_at_epoch`，原始 `sources/expected_nodes/process_inventory`、四类 inspect replies（active/reserved/scheduled/active_queues）、Redis 全优先级 ready 列表与 unacked/index、已知任务原始终态审计和 ledger 活动。源/节点缺答、不明归属、未知队列、任何目标活动均拒绝；服务在提交前重新采集，不消费、ack 或清理消息，不启停宿主。JSON broker body 仅作受限解析，不反序列化 pickle。

`sources.fingerprint` 默认 `file_sha256`，只对固定 runtime_pool 主源允许 `runtime_ownership_v1`：其 size/SHA 指完整 slots/lost 所有权规范化 JSON 投影，而非原文件。投影保留 worker 身份、host publication 版本、generation、root、queue、workspace、资源生命周期与完整覆盖；只排除 active/pending/时间，并把同一所有者的 IDLE/BUSY 合并为 OWNED。foreign 正常任务进度不使批准范围漂移；新增/丢失 slot、进程出生时间或所有者变化仍拒绝。`process_inventory` 独立包含实际 legacy/slot worker/发布宿主及完整 PID/create_time/cmdline/runtime 身份，并与全进程集合对账。原任务的 per-task 终态审计保持完整文件指纹，不锁定共享 append-only MCP 调用日志。

接管复用正常分配锁和工作区锁，保留全部原文件；先持久化 exact `audit/{original_task_id}.json` 非终态查询路由，再准备原生 operation 的 SUCCESS state、原始 result 和 REPORT，最后将已 fsync 的同目录候选以 hardlink 原子发布到 `.info/run_state.json`，作为唯一提交点。Windows NTFS 与支持硬链接的 POSIX 文件系统均在目标已存在时拒绝；不支持 hardlink 时失败关闭，不回退到可能覆盖的 rename/replace。冲突保留外来身份、准备候选、operation 和原 ID 路由；未知发布返回仍以原 ID 查询真实 commit witness。未提交的预写 SUCCESS 不代表接管成功。提交后，即使外层审计追加失败、HTTP 回执丢失或审计写入 ERROR/半行，公开 `get_task_result` 仍按原 ID 验证 canonical 路径、commit witness 和原始结果恢复真实终态，无 Redis 依赖且不重放业务。提交前失败仅清理本次新建元数据；发布结果不明则保留证据，禁止覆盖原有身份或重放。

Dashboard 取消先持久化 `CANCEL_REQUESTED`，执行器在开始执行或下一次 API/修复/重试/工作流段之前确认 `CANCELLED`；保留已完成结果，不回滚。正在执行的单次 DCC 调用或原生批次不可中断，若它完成了全部工作，则返回实际完成结果。取消不强杀共享 Worker，也不撤销队列消息，以便排队任务仍可由执行器确认停止。列表、详情、MCP 与 SSE 共用审计状态；只有任务级收尾是终态，步骤失败不提前结束观察。SSE 观察超时为 `WATCH_TIMEOUT`，不代表执行超时。长驻服务须加载此版本后才具备上述确认行为。

## 运行环境

- Python：共享大脑环境 `Y:/GGbommer/scripts/.conda_envs/brain`（Python 3.11），CGI 与大脑及其他本地工具共用。
- Redis：Notes 受管 `Tools/_managed/redis/redis-server.exe`。
- Worker：按工作区分配 `cgi_slot_*` 队列和各自的 solo Worker、DCC warm pool。`CGI_RUNTIME_MAX_SLOTS=3`、`CGI_RUNTIME_MIN_FREE_GB=8`、`CGI_RUNTIME_THREADS=6` 可配置；线程和内存为预算/准入，非操作系统硬配额。旧 `cgi_queue` 保留未绑定只读/UE旁路与迁移兼容。
- DCC：Maya 2025+、Blender 5.0+、UE 5.x；路径通过 `.env` 和 `config/{project}_config.json` 配置。

## 快速使用

本地 AI 客户端使用标准 STDIO，由客户端启动入口进程。所有支持 STDIO 的客户端使用同一命令；不需要专门的 AI 适配器或预先启动 HTTP 服务。Python 环境须已安装本仓库及依赖（本机共享大脑环境已安装）。

```json
{
  "mcpServers": {
    "cgi_pipeline": {
      "command": "Y:/GGbommer/scripts/.conda_envs/brain/python.exe",
      "args": ["-s", "-m", "cgi_pipeline.server.server"],
      "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    }
  }
}
```

这是常见客户端的 JSON 表达；具体外层配置格式由客户端决定。无需指定工作目录，仓库配置和审计路径从安装位置定位。标准输出保留给 MCP 协议，诊断写到标准错误或日志。

客户端还需能发现 `cgi-pipeline-operator` 操作 Skill；MCP 连接成功并不表示 Skill 已安装。本机唯一维护正文为 `C:/Users/yuweiming/.agents/skills/cgi-pipeline-operator/SKILL.md`，总目录以「CGI Pipeline 操作 Skill 维护源」精确登记；正常维护仍须有效授权任务。Claude 的 `C:/Users/yuweiming/.claude/skills/cgi-pipeline-operator` 使用既有 junction 读取同源；Codex 直接发现个人源。该源位于本机用户目录，不受 Notes/CGI Git 跟踪，不能据此宣称另一机器或重装后可自动恢复。Notes 的 `sync_skills.ps1` 只同步 Notes 自动治理技能，不负责这个项目操作 Skill。观察工具选择、实际阅图和编辑前后核验由该 Skill 的 Observe and verify 编排；参数和副作用仍看 `api_help`。接入验收须在新客户端会话实际采用 Skill，再核工具调用、图像显示与判断；已有客户端可能需要重新发现技能与工具。

MCP 入口随客户端退出；Redis、工作区 Worker 及其后台 DCC 会话按需启动并跨客户端复用，不随入口退出关闭。新隔离后台使用目录里的 `pipeline.runtime.status/release` 管理；旧全局维护只处理 legacy，跳过所有带 runtime 标记的宿主。MCP 断开不代表已提交后台任务取消，可用 `get_task_result(task_id)` 恢复结果。

Windows 共享服务优先脱离客户端 Job 启动；遇到禁止脱离的 Job 时，通过本机 WMI 创建隐藏进程（使用 MCP Python 环境已有的 pywin32）。此路径传递同一环境并继续检查服务就绪，不安装 AI 专用计划任务；若本机策略禁用 WMI，则返回启动失败。Linux/macOS 使用独立进程会话，此次真实验收仅覆盖 Windows。

需要远程访问或独立 HTTP 服务时，给相同入口添加 `--http`，默认监听 `http://127.0.0.1:8000/mcp`；HTTP 与 STDIO 共用上述运行时。

多会话共用同一个 HTTP 入口。不同目标的调用可并发，同一 Maya 端口仍串行。
通过 `bin/manage_mcp_http.ps1 -Action restart -Port 8000` 更新时，管理器先停止受理新工具调用，
等待当前执行线程、异步前台工作和审计收尾完成，再正常退出 HTTP 前端；已派发的独立 Worker/DCC 保留。
排空超时会保留原服务并恢复受理，不会自动升级为强制终止。更新存在短暂重连窗口。
排空期间 `HTTP_DRAINING` 且 `submitted=false` 表示该次调用尚未受理，可在恢复后重试；
已有 task_id 的任务始终按原 ID 查询，不重新提交。

旧前端尚无排空协议时，正常 restart 会明确拒绝；首次迁移必须先协调当前使用者，
再以管理器的 `-InitialProtocolCutoverReceipt` 提交匹配进程身份、短期有效且含协调确认的单次回执。
控制凭据只保存在 `runtime/mcp_http_<port>.control.json`，不进入命令行或公共状态输出；
能读取该文件的账号属于受信任管理者。

```powershell
$py = "Y:\GGbommer\scripts\.conda_envs\brain\python.exe"
& $py -m cgi_pipeline.dashboard.app
& $py -m cgi_pipeline.cli list-apis
& $py -m cgi_pipeline.cli api-help blender.asset.blender_export_abc
& $py -m cgi_pipeline.cli run-workflow tex_to_rig_verify_and_sync --project ysj --asset ciweiguai --param mode=geometry_materials --param save_result=true
```

服务管理器会按需拉起 Redis 和 Worker。前台执行必须在参数中显式传 `execution_mode=foreground` 与 `foreground_port`；后台工作区自动选择独立通道，同工作区串行。

任务沙盒与业务项目可独立指定：`project="ysj", workspace_project="default", business_task_id="PL284"` 使用 YSJ 配置，文件位于 `projects/default/PL284`。省略 `workspace_project` 保持旧调用布局；已有任务绑定不自动迁移。CLI 对应 `--workspace-project default`。

自动 workflow 使用操作独有的临时后台，不占三个驻留名额；物理内存不足时自动等待，不关闭其他任务。成功保存并验证文件后自动关闭自身，失败保留场景与诊断，回执 `workflow_runtime` 区分执行和收尾。同步工作流明确选择 `save_result=false` 继续编辑时保留驻留场景。名称取校验后的原始源名，正常保存按末尾 `v001 → v002` 递增。完整边界见 [运行时契约](docs/architecture/pipeline_runtime_contract_v1.md)。

API/batch 默认等待结果；前台 `wait=false` 也会先返回 `SUBMITTED/task_id`，随后通过 `get_task_result` 恢复同一执行。前台后台线程依附 MCP 服务进程，退出前应等待结果；客户端断开/超时不能当作操作未发生，勿重新提交。UE 场景 API 经已有 UE_MCP_Bridge 的编辑器 Python 通道运行 canonical handler，不新增执行器；仍须目标编辑器能导入当前 CGI 源码及依赖。

等待与回执预算：execute_api/batch/workflow 的等待和 `get_task_result` 的服务端等待都截断在客户端工具超时之前（`CGI_MCP_MAX_WAIT_SEC`，默认 110 秒，对应 Codex `tool_timeout_sec=120` 减 10 秒）；到点返回 `PROGRESS` 和原 `task_id`，再调 `get_task_result(task_id)` 继续等待，`wait_sec=0` 立即读当前状态。公开工具结果超过 4 KiB 时默认只返回摘要（状态、错误、产物路径、小字段 output、大字段的大小与结构、`receipt_ref`），`detail=true` 返回全量；全量结果里单个回执仍受 64 KiB 内联预算，超出部分按 `audit_path`/`entry_index` 读原审计。`audit/mcp_calls.jsonl` 每行记录 `response_bytes`。

`list_workflows` 从展开的 workflow 模板及 API 输入元数据生成 `input_contract` 与 `mcp_call_template`，外部输入缺失或类型不符在工作区/Worker 启动前拒绝。源文件也可由已声明的 `source_resolution` 按 project + asset_name 定位，或复用 workspace 已记录来源；不会仅因省略 source_path 拒绝这两条入口。`run_dir/info_dir` 由运行时提供，输出步骤引用留到执行时解析。

`PARTIAL` 表示已结束但不完整，`AUDIT_FAILED` 表示质量检查失败；同步返回、恢复、工作区与报告保留业务状态。MCP 调用审计仅保存返回的任务/工作区/业务 ID、错误码和必要时间，不复制参数、代码或结果全文。`dispatch_return_ms` 包含提交函数内的等待（同步前台也含执行），`recovery_wait_ms` 是后续恢复轮询等待，`api_execution_ms` 来自 receipt；这些边界不同，不相减推断网络或 Hook 开销。

每次调用保留独立 `task_id` 和原始审计，沙盒按需创建。UE 现有编辑器调用、只读 Pipeline API（含会话发现）及它们的同运行时 batch，在没有显式 `workspace_id`、`source_path` 或 `scene_action="open"` 时不创建沙盒、`.info` 或额外 Markdown 报告；通过 `get_task_result(task_id)` 恢复回执和 `audit_path`。是否进入此路径由服务端读取 API 目录契约判断，不依赖模型传入跳过标记。

需要文件隔离的后台场景处理和资产工作流仍创建任务工作区。已有 Notes/生产任务 ID 时传 `business_task_id`（如 `PL251`）：同一 `workspace_project + business_task_id` 自动创建或复用 `projects/{workspace_project}/{business_task_id}`；省略 `workspace_project` 时沿用 `project`，业务 `project` 仍选择项目配置。没有稳定外部任务 ID 时，保留返回的 `workspace_id` 与存储命名空间供后续单 API、batch、workflow 共用。每次调用仍有独立 `task_id`；为兼容仍驻留的 Worker，新操作原生使用 `.info/operations/{task_id}/` 保存独立报告及 state/result/manifest，附属报告按任务与步骤命名。根 `REPORT.md` 展示操作时间表及最新提交操作的完整明细，晚完成的旧任务不会顶替最新详情。既有平铺 `.info/{task_id}_REPORT.md`、`{task_id}_{state,result,manifest}.json` 和目录布局记录均继续原址读取和恢复，不迁移、不双写；同 ID 双布局冲突明确拒绝。普通 UE／Pipeline 调用也可显式归入已有工作区。`business_task_id` 负责业务归组，`workspace_id` 负责精确恢复，新的执行 `task_id` 不能替代两者，也不按 DCC 端口自动合并不同任务。

默认 `scene_action="continue"`：首次 Maya/Blender 后台操作复用当前工作区内的 `source_path`，或复制外部来源后打开。完整调用集合全部为 Maya/Blender 的 `inspect/preview` 时，未选来源或已有工作区会返回 `SCENE_TARGET_REQUIRED`；也可选择前台会话，或显式 `scene_action="open"` 创建空的隔离场景。`apply` 调用可初始化场景；既有 Pipeline→DCC 组合仍由其来源解析步骤提供目标。这是保守的场景初始化政策，`access` 和 `modes` 都不代表无副作用许可，实际影响见各 API 的 `effects`。之后使用 workspace_id 直接执行请求的 API，保留未保存修改，不再次定位资产、复制、打开场景或运行整条 workflow。每次查询读取当前状态，不缓存旧权重。资产定位由明确的 API 或 workflow 步骤执行；运行已知 API 不需要重复发现目录或调用 workflow。

场景被其他任务替换、进程退出或初始化失败时，续接返回 `WORKSPACE_SCENE_UNAVAILABLE`、`RUNTIME_LOST` 或具体代次错误，不会自动重放。用 `scene_action="open"` 与已保存的实际 `source_path` 明确恢复；空路径表示新场景。每个工作区通道的 Maya/Blender 各保留一个常驻场景，不同工作区使用不同通道。需要跨服务重启恢复的修改应显式保存到当前沙盒。共享业务目录不参与旧任务目录的自动 TTL 删除。

保存和释放分别调用。Blender 保存的 `SUCCESS` 表示原生保存与实际文件验证通过，输出 `save_completed`、`artifact_verified` 和原始 `native_dirty`。后台模式不消费原生保存通知，`clean_state` 保持 `UNKNOWN`；默认释放拒绝未知状态。明确选择 `discard=true` 才会放弃所属驻留状态，保存成功不自动授权丢弃。Maya 与交互 Blender 继续采用实际脏状态检查。

前台默认操作选定端口的现有场景，不创建后台沙盒；显式传 workspace_id 时只把该前台操作归入已有业务目录，不会把后台场景自动搬到前台。前台不支持 `scene_action="open"`，明确的开场景动作仍由对应 API 执行。

已授权的增量任务直接使用用户指定的实际工程，不强制复制工程或另开隔离测试场景。客户端可为该任务启动、正常关闭或重启目标 DCC；启动前核实可执行文件和工程路径并优先复用匹配会话，关闭前妥善保存未保存内容，不强制结束无关会话。有登记的生命周期 API 时优先复用；没有时可用宿主进程启动入口及应用正常关闭机制，启动后重新发现并核对会话，场景与资产操作继续通过 CGI。后台场景处理的 staging 契约和受保护路径权限按原契约执行，不能把它们扩大为所有 DCC 操作都必须隔离。操作技能的维护源与客户端同步方式见本节上文。

## API 目录

FastMCP Client 对错误结果不填充便捷字段 `data`；错误回执从 `structured_content` 读取。

MCP 出口按唯一状态 owner `src/cgi_pipeline/core/task_status.py` 的 `is_failure` 将顶层失败回执标记为 `isError=true`，同时保留原始结构化回执及内容。失败分类复用既有终态，排除成功和需要关注的状态，并明确保留边界失败别名；不改变原有终态与轮询行为。`PARTIAL`、需要关注和运行中状态保留各自语义；调用方仍须检查业务状态，不能仅以 `isError=false` 判断任务已成功。读取失败回执的客户端应关闭自动抛错（FastMCP Client 使用 `raise_on_error=False`）。此转换集中在服务传输边界，不改变 API handler 返回值，也不递归把局部步骤失败当作整体失败。

稳定公开入口：

```python
from cgi_pipeline import api, client, tools
```

`api.execute_local()` 在匹配宿主内直接执行已登记能力，使用同一参数、模式与 receipt 校验核；它保留当前宿主场景，不经过服务队列或自动 staging。`client.execute()` 才加载 FastMCP 并调用远程服务。服务端明确拒绝 `execution_mode=local`，宿主直执使用 `api.execute_local()`；前台和后台按各自契约执行，不静默互换。每个能力目录包含：

```text
src/cgi_pipeline/capabilities/**/capability.yaml  # 唯一机器契约
src/cgi_pipeline/capabilities/**/*.py             # 同目录 handler/实现
src/cgi_pipeline/data/*.json                      # 生成的运行时目录
```

`api_help(api_id)` 返回生成目录中的 manifest 契约及 `catalog_version`；修改 `capability.yaml` 后运行 `tools/build_catalogs.py`，治理检查会拒绝过期 JSON，不保留第二份手写参数文档。

`api_help` 的 `mcp_call_template` 从公开 `ExecuteApiInput` 模型及 manifest 输入派生，`arguments` 包含真实 MCP 外层 `params`、`api_id`、内层 `params` 与 `expected_catalog_version`。`requires_binding` 列出必须用实际任务数据替换的路径、类型和说明；其中 null 是未绑定占位，绑定前不可执行。还须按前置条件确认会话、项目与场景上下文，默认值不代表已经选定实际会话。manifest 原 `help.usage` 是语义简写，不是 MCP JSON 示例。公开外层未知字段会被拒绝；搜索参数是 `search`。executor/access/surfaces 的公开枚举与 manifest 校验共用 `contracts.py` 集合；非法固定枚举报错，合法过滤无匹配仍返回空目录。

`catalog_version` 标识已发布的 manifest 目录，不涵盖 MCP 工具 schema、Skill 或会话端口有效期。目录读取检查生成 JSON 的路径、修改时间和大小；未变化复用缓存，变化后校验并整体替换内容与版本。`api_help`、一次执行和一个 batch 各自固定同一目录快照，宿主收到过期版本会在执行 handler 前拒绝。新增 API 生成目录后可被已升级服务自动发现，无需为每次登记重连。MCP schema 或框架代码升级仍需重连服务；端口及会话归属按真实会话状态重新确认。

Maya 前台复用健康 RPyC 连接及已注入的单次/批次入口；热调用不另开 TCP 探活连接。失效连接在下一次调用重建，已经派发的业务操作不会因传输失败自动重放。正常连接 INFO 默认不显示，警告、异常及业务错误回执保留。能力模块仅在发布目录版本变化时刷新，Maya、UI 与核心框架模块保持状态。修改 `cgi_pipeline.capabilities` 内实现时必须提高对应 `capability.yaml` 版本并重新生成目录；外部工具源码、公共 UI 或核心框架变更按其受控更新入口或重启宿主生效。目录版本相同不会监视、扫描或重载能力源码。工具架只由明确打开或刷新动作重建。

`list_apis` 默认返回 20 项；按 executor/capability/operation/stages/targets/access/surfaces/execution_modes/modes/effects 及 search 过滤，使用 `next_cursor` 续页。同一维度多值取 OR，不同维度取 AND；游标绑定目录版本和全部归一后的过滤条件。旧查询 `joints`、`anim_curve/animation_curves` 分别归一到 `joint`、`animation_curve`，manifest 只登记规范词。已知 API 按上述版本契约复用，不重复发现目录。

### Maya 场景观察

先明确问题和当前场景身份，再逐层查询。全场景概况会遍历网格做统计，只在需要全景信息时调用；局部问题先用有界节点查询定位长路径或 UUID。

- 层级查询在排除 shape 时仍保留 joint 等 transform 派生节点；深度限制和完整路径按 `maya.rig.maya_get_hierarchy` 契约处理。
- `maya.asset.maya_get_object_info` 对 transform 的单个 `mesh` 选择首个非 intermediate 输出；明确查询 mesh、Orig 或 camera 时读取该 shape 自身，不把多 shape 统计当作合计。字段失败以 `null`、`query_errors` 和 `PARTIAL` 呈现；真实零拓扑仍为0。空 mesh 的原生 bounding box 可能是无效的极值哨兵，不能据此判断真实空间范围。
- `maya.asset.maya_get_scene_info` 的 `collections` 明示列表总数、返回数、上限和截断；拓扑查询失败时用 `geometry_totals_complete` 及失败计数说明总数不完整。统计沿用全部 mesh shape（含 intermediate）的范围，不能直接解释成最终渲染几何。小夹具计时不代表生产大场景性能；全场景扫描的成本边界保留。
- 层级与绑定签名复用有界 `maya.rig.framework.inspect`；候选签名不是绑定归属或功能正确的证明。
- 先用 `maya.scene.attributes.inspect` 发现属性名称和声明 schema，再用现有属性查询读取选定值。schema、当前值、锁定状态与实际求值行为分别核实。
- 从明确根节点查询 DG 连接或局部快照，按问题选择 upstream、downstream 或 both。检查 `truncated`；存在遗漏时缩小范围或继续查询，不把部分图解释成完整场景。plug 连线不等于节点内部算法或完整 Evaluation Graph。
- 变形历史、引用、材质、拓扑和时间采样沿现有领域 API 查询；涉及行为与外观时，用数值或截图交叉核验。
- 不熟悉的命令先核目标 Maya 版本，通过既有代码 API 查询实时 `cmds.help("命令名", syntaxOnly=True)` 或模块信息，再核对该版本官方文档。查询配方留在任务工作区，不引入另一套 MCP、注册表或参数目录。
- 按各 API 的 `effects` 选择观察手段。`maya.asset.maya_capture_viewport` 保留默认整窗 `scope=window`，用 `scope=viewport` 与明确 `panel` 获取当前视口；返回 PNG 和场景、相机矩阵、帧、显示条件、实际尺寸。绑定任务时图片进入该任务 outputs/captures。仅支持 GUI，不自动改相机、时间或模型；完整参数仍以 api_help 为准。
- 视觉判断前必须打开返回的 PNG；文件存在或节点图查询成功都不证明已看见模型。图片是某一时刻的样本，不证明持续感知、遮挡面的状态或真实 UI 交互。多视角、局部特写及同条件前后对比按问题组合已有接口；报告分别说明观察事实、推断与未核范围。

日常观察优先让 Maya 完成定位：已知完整路径/UUID 就直接查目标；看不清时使用已授权的相机移动、原 `maya_select_objects` 聚焦与 `view_restore`，或经既有代码入口调用原生 `isolateSelect` 并恢复原状态。未知画面区域先取普通 viewport PNG、实际阅图，再用原 `maya.scene.viewport.pick` 的 `mode=native,panel,pixels`；可选 `expected_view` 接普通截图的 `output.observation`。该路径直接调用 Maya `hitTest`，只查询命中候选的身份，不遍历全场景 mesh 或顶点。

native 返回 Maya 当前控件的全部候选及未解析项，保持完整实例路径；不认证深度排序或渲染像素归属，也不是 strict guard token。原生显示/过滤/可选性可能使可见区域没有候选，不能据此宣称模型不存在；可改为直接查询已知对象、聚焦或隔离后复核。多个候选不擅取首项。无文件的前台查询/视图操作保留匹配的 `business_task_id` 与 project、不传 `workspace_id`，复用既有 TASK_LINK 审计链以省去工作区报告刷新；需要任务图像/文件时仍传原 workspace，不新建环境或通道。

只有明确需要历史 PNG 与完整 polygon 状态一致，或需要其严格放置编辑 guard 时，才使用以下 `mode=verified` 流程（原默认契约保留）。参数唯一来源仍为 `api_help`：

1. 用 `maya.scene.nodes.find` 缩小候选，再用 `maya.scene.nodes.resolve` 的实例身份固定完整 DAG 路径、UUID 与会话。名称含 hair 只代表检索候选，实际语义仍须看图并核对所属层级、材质或项目约定。
2. 需要改变视图且已获授权时，用原 `maya.asset.maya_select_objects` 传完整目标路径、`panel`、`frame=true`。保留整个 `output.view_restore`；独立可写相机才能取景，组件选择或共享、驱动相机明确拒绝。
3. 用原截图 API 传 `scope=viewport,panel,grounding=true,targets`。实际打开原 PNG，保存 **`output.grounding.reference`**；普通 `output.observation` 只是元数据，不能作为拾取或编辑前置。grounding 读取完整有界可见 polygon 候选，超预算拒绝，不能截断遮挡者。它比普通取图多出几何校验成本。
4. 对实际看见的区域，用 `maya.scene.viewport.pick` 传 reference、原图左上角起算的整数 `[x,y]` 和 `expected_target`。逐项检查状态及 `expected_target_match=true`；receipt SUCCESS 不等于目标匹配。投影在画内不证明可见。最近表面外观不支持时返回 `UNSUPPORTED_PIXEL_APPEARANCE`，保留候选身份但不猜后方对象；场景其他区域存在透明物体不使所有像素失效。多个实例在声明深度容差内重叠时返回 `AMBIGUOUS_POLYGON_GEOMETRY` 与全部候选、空匹配，不按枚举顺序选择。容差只用于保守拒绝歧义，不认证 GPU 最终像素。
5. 已授权的普通叶 mesh transform 放置编辑，可在原 `transforms.set_many` 或 `attributes.set_many` 传 reference、每项 `expected_uuid` 与 `expected_before`。这是限定 TRS 的可选 guard，不能替代 rig、任意 DG 或顶点编辑的领域验证。编辑后旧 reference 失效；保持相机、帧和显示条件重新取图，实际阅图并比较目标及非目标数据，不分别自动取景掩盖位移。
6. 完成检查后，向原选择入口同时传明确的 `panel`（使用 `capsule.before.panel`）和完整 `restore_view` capsule；只传 capsule 会被预检拒绝。恢复前比较当前状态与预期后态及选择 UUID；冲突时保留错误，不强制覆盖用户之后的操作。失败编辑须检查 guard 的实际完成数、回滚与回读证据，不能把 Undo 调用成功当作整个场景恢复。

grounding 当前限定 Windows Maya GUI 的原生 polygon 几何及显式视口；普通 locked activeList 是观察来源，不单独判为过滤。XGen、插件绘制、平滑预览、模板/隔离/过滤等按契约拒绝；透明和未知材质按最近交点返回不确定。几何对应不认证任意 shader/贴图/灯光变化或最终渲染像素。新 API 的实机验收范围以 manifest verification 与任务原始回执为准。

成本按问题选择：日常取图和当前对象定位使用普通截图与 native；明确需要上述历史原图验证时才启用 grounding。多个点一次传入 `viewport.pick`（1..20 点）；verified 整个批次共享前后两次完整校验，避免逐点重复调用。严格跨 API 状态校验保留，不能把上次快照当作仍然有效的缓存。

实现只在当前调用内去重：一次 snapshot 内共享 SG/shader 查询，实例自己的赋材和几何仍独立读取；一次像素批次按完整 DAG 路径复用 MFnMesh，下一请求重建。几何指纹按最多8192值分块编码，保持原小端字节与SHA-256；不省略数值检查，不缓存跨请求几何或材质，也未引入原生求交加速器缓存。真实性能及图像差异以 `projects/default/PL297/outputs/ACCEPTANCE.md` 的同条件验收范围为准。

### CGI 共享窗口观察

Windows 观察归 Pipeline 共享工具层：`pipeline.window.discover` 定位窗口，`pipeline.window.capture` 获取 PNG，DCC 与普通软件共用这两个入口。默认 `scope=window` 绑定明确窗口/进程身份；发现时 `include_monitors=true` 返回显示器身份和物理坐标，截图时 `scope=desktop` 取得全部或指定屏幕的独立 PNG，并默认生成保留负坐标、竖屏和透明屏外间隙的合成图。可把发现的完整 monitors 绑定为 `expected_monitors` 拒绝过期拓扑。各屏在同一隐藏 worker 内逐一采集，带独立帧时间，并非同时帧。取图后实际阅图，按任务作合理判断；图像不是持续视觉、软件内部对象语义或编辑权限。参数以 `api_help` 为准。

共享窗口图可用于普通软件，不限 DCC。它没有 Maya 原生几何与投影绑定，不能作为 `viewport.pick` 的 observation；需要像素到场景对象对应时使用上述原生 viewport grounding。

Windows 普通控件可由系统 [UI Automation Control Patterns](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controlpatternsoverview) 提供文本、值、选择和 Invoke 等语义，取决于应用暴露的 Provider；它不提供 GPU 视口中的模型语义，操作也不保证不改变焦点。当前 CGI 共享层只实现窗口发现和取图，未实现 UIA 读写。需要操作时，优先复用应用原生 API；网页用已有浏览器入口，普通控件再评估 UIA，自绘 DCC 场景继续用对应 DCC API，无须为此另建 MCP。[WGC 单窗捕获](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow) 已是当前按 HWND 后台取图机制；PrintWindow 依赖目标同步绘制，Desktop Duplication 针对显示输出，不能据此宣称比现入口更通用可靠。

捕获复用已安装的 windows-capture 2.0.1/WGC，在独立隐藏 worker 中执行。默认最小化策略为 `reject`，不改窗口；存在可用浏览器/应用原生内容入口时优先复用。确需窗口像素且授权允许时，显式 `minimized_policy=restore` 用非激活显示、取图、还原原 placement 和最小化状态；恢复分支默认在首帧后收集750ms内的后续帧，保留最新帧、实际帧数和时间，不宣称网页已加载。成功、失败及 worker 超时均尝试还原；用户激活或移动目标时不覆盖新动作，返回恢复错误与状态证据。临时恢复可能可见，不能承诺无视觉变化。没有输入注入、强制焦点或自动普通最大化；整屏捕获不需要恢复任何窗口。Windows10保留系统捕获边框。worker 的超时/回收与 CGI 排队、磁盘、传输耗时分开。

捕获前先检查 CGI 当前进程所在会话的 WTS 连接状态。非 Active 或查询失败时返回 `WINDOW_CAPTURE_SESSION_UNAVAILABLE` 与观察结果，不启动捕获 worker；窗口发现仍可用于诊断。合法的 Active 远程桌面会话可以使用，不要求等于 console session。Active 不证明桌面未锁定，前台窗口为零也不单独证明锁屏。

PNG 输出至当前任务 `outputs/captures`，返回窗口身份、实际尺寸、帧时间与前台窗口前后观察。报告视觉结论前仍须打开图片；系统级捕获的实际兼容性以各软件的实测回执为准。

PipelineWorker 复用既有 `capability_session` 在目录版本变化时刷新第一方能力模块并固定调用快照，避免新契约落到旧 handler；框架自身变更仍按运行时契约受控刷新。仅看到 `catalog_version` 或回执版本号不证明实现已加载，须核新增输出、worker 身份和真实图像。

本机扩展验收：2026-10-22，正式MCP三屏取得2560×1440、2560×1440、1080×1920及6200×1924合成图并实际阅图；最小化Chrome通过显式恢复分支取得1080×1880图，原位置/flags/show state均恢复。证据与新会话验收见 `projects/default/PL298/outputs/ACCEPTANCE.md`。其他软件最小化、HDR、混合DPI和锁屏仍按实际契约与测试判断。

既有焦点专项验收：2026-10-21，Windows10 Pro19045 / Maya2025.3 两个非最小化主窗取得2560×1400新PNG，主代理及独立审查均实际阅图。一次完整16.106秒MCP调用内，前台事件Hook与518个非零采样均保持ChatGPT，无前台事件，最大采样间隔32.291ms；另一次调用有前台变化，归因未定，不能据端点或用户“未观察自动跳转”抹去。用户反馈未见自动切到Maya再返回。限定证据见 `projects/default/PL294/outputs/final_observation_v1/ACCEPTANCE.md`；早期样本与全零采样等边界仍保留在 `CONNECTED_CAPTURE_REPORT.md` 与 `REPORT_PERFORMANCE_FIX.md`。

性能修复：既有task_report_writer将init/final各合并为一次完整发布及锁外摘要刷新，正常成功路径摘要刷新10→6次；workspace仅在单次render内复用核实后的路径/state，保留全部候选保护及latest读取前的新检查，无跨调用缓存。同一30记录完整刷新中位1.503→0.456秒，输出一致。当前Codex旧连接一次往返8.127秒（改前18.779–22.038秒）；专属新登记STDIO三次call_tool为4.656–5.484秒，后者不含连接启动与Codex外层交付开销。共享worker已受控更新，其他旧STDIO各自正常重连后加载入口侧改动。诊断基线见 `projects/default/PL294/CAPTURE_LATENCY_DIAGNOSIS.md`，完整修复与计时边界见 `projects/default/PL294/REPORT_PERFORMANCE_FIX.md`，不作通用速度保证。

### SDK 开发分工

分类是同一份能力契约的不同视图，不按标签复制实现或建立第二套注册表：

| 维度 | 表达什么 |
|---|---|
| executor | Maya、Blender、UE 或 Pipeline 执行目标 |
| capability、operation | 功能域与具体动作 |
| stages、targets | 生产环节与作用对象 |
| access、effects | 能力整体的读写等级，以及条件明确的场景、文件、宿主等实际影响 |
| execution_modes、surfaces | local/foreground/background 策略及可调用入口 |
| modes | inspect/preview/apply 操作意图；多模式通过 mode_binding 映射原有业务输入 |

`mode_binding` 的 selector 沿用原 `action/operation/mode/dry_run` 等业务参数，typed cases 必须穷尽输入域及声明模式。`inspect/preview` 可以输出报告、切换宿主状态或产生诊断场景数据，必须读 `effects`，不能由名称推断只读或自动保存。

无状态操作保持 `(params, context)` 函数 handler；算法、宿主操作、I/O 和 UI 按职责分模块。持有资源或跨调用状态的对象用 class 管理生命周期：Maya/Blender 后台 worker 组合同一个 `hosts/file_ipc.FileIpcTransport`，宿主启动、退出和超时策略留在各 worker；Maya 前台连接使用自己的 owner。完全相同的领域辅助归 `rig_ops_common` 等既有公共模块。优先共享实现与组合，只有替换契约相同才继承，不为每个 API 包一层 class。

新增或修正能力沿 `capability.yaml → tools/build_catalogs.py → catalog → execution → handler → receipt` 一条链交付。先复用现有实现，再改直接缺口；实现变化同步能力版本，框架变化按运行时契约刷新对应进程。先完成直接相关测试和目录一致性，再用代表性真实入口验证，静态通过、测试通过和真实 DCC 验收分别记录。工作区、保存与更新边界以 [运行时契约](docs/architecture/pipeline_runtime_contract_v1.md) 为准。

`outputs` 使用 `{实际顶层字段: 非空说明}`，列出各操作分支可能返回的字段；字段不保证每次全部出现。以真实 `receipt.output` 为准，不能把场景修改写成不存在的场景文件输出。`summary/items` 也只在实现实际提供时声明。输入定义、显式值和默认值使用同一类型及 choices 校验；公开目录查询返回独立副本。Receipt 必须可序列化为 JSON，错误字段和 artifact 路径使用字符串，耗时是非负有限数。

`packages/registry.yaml` 只登记可运行工具的身份、分类、源码位置和宿主入口。完整工具仓由用户维护，留在自己的独立仓库；CGI 引用源码位置，不再复制一份。用户仓地址直接 clone；原作者地址先取得源码，再导入用户指定的新仓，维护 origin 指向用户仓；来源、许可证和必要版权说明仍保留。经批准的原子能力迁移可从锁定 commit 选择性保存源码，但须转换为 CGI capability，并保留许可证、原路径和 blob 证据；上游 MCP 服务和运行框架不进入 CGI。

## 三宿主工具接入

Maya、Blender、UE 共用 package 登记、公共 Python 加载器和部署一致性检查；宿主启动、UI、二进制及业务验收分别适配。工具源码只有一份：可复用代码在 Notes 的 `DCC/<host>/<feature>`，独立维护仓在 `Tools/_managed/<repo>`，项目策略留在 `Projects/<project>`。跨宿主工具登记多个 hosts，不按标签复制仓库。

分类采用 DCC、环节、模块、功能、形态五维。已登记 package 的 hosts/stages 由 registry 拥有；工具卡补 module/functions/kinds，部署 JSON 只写实际加载信息。分类依据来自真实入口、依赖和副作用，证据不足标待核实；不能从仓库名猜测后直接标成可用。工具、库、宿主 SDK、原子 API、workflow、工具架保持各自职责，工具入口存在不代表已有同名原子 API。

```powershell
# 在 CGI 仓库运行；筛选不隐藏其它部署错误。
& $py tools/check_tool_deployments.py --host blender --module cache
& $py tools/check_tool_deployments.py --host ue --function bake
& $py tools/build_catalogs.py --check
```

检查器读取 registry 的 `deployments` 指针，校验工具卡、源码、宿主、入口、版本和 API 引用。当前四个部署条目是 XGen、Perseus、Cache Sequence Binder、YSJ Crowd；公共 package 目录另含既有 adPose，Perseus 通过原生插件部署，未伪造 Python package/API。

### 安装与调用

全部共享入口在 Y 盘。Maya 环境安装仍由 Notes `DCC/maya/environment/install.ps1` 管理；CGI SDK 由 `deploy/maya/modules/CGIPipeline.mod` 暴露。Blender/UE 共用下面的安装器，默认只预览，合并用户环境变量，保留其他路径，回执在 `runtime/installations/`：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File deploy/install_tools.ps1 -HostName blender -Action Preview
pwsh -NoProfile -ExecutionPolicy Bypass -File deploy/install_tools.ps1 -HostName blender -Action Install
pwsh -NoProfile -ExecutionPolicy Bypass -File deploy/install_tools.ps1 -HostName ue -Action Install
# Check 检查；Uninstall 撤回安装器拥有的路径；撤回后重启宿主。
```

Blender 使用 `BLENDER_SYSTEM_SCRIPTS` 指向 `deploy/blender/scripts`，启动模块接入 SDK 路径，并在 GUI 会话自动启动 CGI 自有前台桥接；后台会话不启动监听。UE 使用 `UE_PYTHONPATH` 指向 `src`，目标工程需启用 PythonScriptPlugin。采用各宿主原生环境机制（[Blender 部署说明](https://docs.blender.org/manual/nl/5.0/advanced/deploying_blender.html)、[UE Python 说明](https://dev.epicgames.com/documentation/en-us/unreal-engine/scripting-the-unreal-editor-using-python?application_version=5.7)）。不向宿主加入 Brain 的 site-packages。已有 DCC 和启动器保留旧环境，安装后重新启动宿主及其启动器；本轮冷启动测试从持久化用户变量刷新父进程环境后启动。

已开启且能导入 CGI SDK 的 Blender GUI，可在 Python 控制台执行一次 `from cgi_pipeline.hosts.blender.native_bridge import start; start()`。默认在 `9876..9885` 自动选择空闲本地端口；需要固定端口时，首次调用改用 `from cgi_pipeline.hosts.blender.native_bridge import start; start(9877)`。后续调用仍通过 CGI MCP，使用 `execution_mode="foreground"` 与该会话的 `foreground_port`。

宿主 Python 控制台或后续指定工具架只调用同一入口：

```python
from cgi_pipeline import tools
tools.list(host='maya')
tools.open('xgen_cache', host='maya')
tools.open('cache_sequence_binder', host='blender')
tools.open('ysj_crowd', host='ue')  # 注册既有 YSJ Crowd 菜单
```

包的 `python_paths` 显式描述 import 根目录；加载前检查验证版本和实际模块来源，同名异源拒绝混用。默认重复调用沿用已导入代码；`reload=True` 只重载入口模块，不递归重载整仓或原生插件。子模块/二进制变更优先使用工具自己的更新机制或重启。Blender 声明 `lifecycle: {blender: addon}` 时检查全部 classes 的注册状态，避免重复注册；`tools.close(..., host='blender')` 注销。其他工具沿用其自己的关闭操作。

### PL233 接入验收（2026-09-17）

| 宿主 | 实际验证 | 证据 |
|---|---|---|
| Maya 2025 | 原生共享模块冷启动；公共入口打开 XGen 窗口、重复打开和显式重载 | `api-2dff7c600848`、`api-e09dd5db2058` |
| Blender 5.0.1 | 5 个 addon 类注册、重复调用、重载、注销；名称归一；GUI 冷启动发现共享 SDK | `api-52cc8352d603`、`api-418d47bb4f1f`；运行目录的 `blender_cold_stdout.log` |
| UE 5.7.4 | 独立空工程注册菜单、重复注册/重载；冷启动直接 import 并注册菜单 | `api-12f397058881`、`api-36b6be4f3174` |

运行目录：`projects/default/20260917_161651_pl233_multidcc_tools/`。34 项公共加载/契约/架构测试、7 项分类检查测试和 11 项 Maya 安装测试通过；114 API / 4 package 派生一致，7 个 MCP 门面治理检查通过。安装预览、重复安装与 Blender 撤回/重装通过。三宿主接入通过不等于 ABC 业务绑定、UE 群集/布料烘焙、Perseus 全绑定流程通过；生产使用另验。用户的 Perseus 维护仓和工具架地址未提供，继续保留为待办。

目录用途由自动生成的 Notes 目录维护：`Y:/GGbommer/scripts/Notes/ai/projects/cgi_pipeline_api_catalog.md`。同步命令：

```powershell
& $py tools/sync_notes_api_index.py --write
```

新增项目只复制并修改 `config/ysj_config.json`，无需改 API 代码。

## 使用中升级能力

用户已授权 AI 在解决 CGI 任务时，根据实际能力缺失和以后复用价值，自主修正现有 API 或新增 API。先复用已知契约和已有实现：参数用错就纠正调用，已有实现不满足契约就修原 owner，可组合解决就组合；只有缺少稳定、可复用的原子能力才新增。一次性项目判断留在任务组合层。

升级须把参数、输出、副作用、错误和恢复方式写入唯一 `capability.yaml`，更新实现及直接相关测试，派生目录并验证真实公开入口；跨宿主能力分别标明已验证范围。可靠的产物/receipt 是依据，REPORT.md 不是自动训练或自动生成代码的入口。修复通过后在当前任务说明“发现了什么缺口、升级了什么、如何复用及验证”，让下次能直接从目录找到。此授权不扩大为删除源资产、外发或修改无关治理对象的授权。

目录生成器同时静态检查 handler 位于对应 executor 下、存在唯一顶层同步函数，并可接收 `(params, context)`；检查不导入宿主模块。函数改名或签名变化必须同步契约，`build_catalogs.py --check` 与治理检查会拒绝失配。静态检查不验证函数业务行为，仍需直接测试和真实入口回执。

## Workflow

当前公开入口、参数和步骤由 `list_workflows` 从已登记 JSON 派生；不将历史名称作为可用入口。Workflow JSON 的每个执行节点填写 canonical `api_id`，段间通过 receipt 的 `output` 传值，不能读取 Markdown 作为机器数据。

公开工作流只有以下两条，控制参数放在 `extra_params`；CLI 使用 `--param key=value`，布尔值写 `true/false`。

| workflow_id | 控制参数与默认值 | 执行步骤 |
|---|---|---|
| `blender_to_maya_full_build` | `pack_textures=false` | 默认5步使用服务器贴图；true为7步，收集贴图、改写JSON、Maya赋材保存，最后自动执行package_asset，输出每资产独立ZIP且只含Maya和对应贴图。 |
| `tex_to_rig_verify_and_sync` | `mode=check`、`save_result=false` | 当前 check 为 5 步（含层级预检/整理）；geometry 为 9 步；geometry_materials 为 11 步；后两者 save_result=true 各增加最终保存一步。check + save_result=true 在创建工作区前拒绝。 |

Blender 材质采集统一按用户确定的 S→X 规则解析贴图：S 盘引用只查 X 盘同相对路径，X 有对应文件就输出 X 路径；X 没有就省略该纹理并沿用原缺图降级，不回退 S、不另搜目录、不保存服务器源 Blend。UDIM 逐 tile 执行，路径改用与缺失写入回执。

`list_workflows` 显示默认分支、参数类型/默认/枚举及可绑定模板。提交与列举使用同一个 `compile_workflow`，控制字段严格校验；未知参数报错。`category/rig_path/source_stage` 等业务输入由原解析器契约承接并保留。提交在绑定工作区之前编译并将 `_workflow_plan` 随队列消息快照化；Worker 只执行这份快照，缺少快照必须重新提交。

模板由 `core/workflow_templates.py` 统一处理编译检查、输入发现和递归求值，支持嵌套对象/列表、数字列表索引和连字符步骤名，例如 `{{outputs.read-asset.items.0.path}}`。完整占位符保留原值类型，混合文本转成字符串；路径及 `replace` 过滤器使用同一语法。缺失引用在受影响 API 派发前终止，保留已完成结果。治理检查使用同一编译器遍历全部有限控制组合和修复步骤，只有明确声明的拒绝组合可以跳过。

结果和报告显示 `effective_parameters`、`save_requested`、`result_saved` 与 `saved_results`。`save_result=false` 的同步只改变当前工作区场景；暂存的源文件不是保存后的结果。只有成功 `save_scene` 回执中的 `output_path` 才计入已保存结果。

## 验证

```powershell
& $py tools/check_pipeline_governance.py
& $py tools/build_catalogs.py --check
& $py -m compileall -q src/cgi_pipeline
& $py -m pytest tests -q
```

最终验收再统一跑代表性跨 DCC Workflow，避免逐个启动 DCC 造成无效等待。
