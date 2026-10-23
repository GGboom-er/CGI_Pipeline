# CGI Pipeline 运行时契约

本文是当前 API/Workflow 运行链的边界说明。单个 API 的参数不在这里重复；未知或版本变化的 API 调用 `api_help(api_id)` 读取对应 manifest。

## 入口与队列

```text
未知合同：list_apis → api_help → execute_api(expected_catalog_version)
已知合同：              execute_api(expected_catalog_version)
多步模板：              pipeline_execute_workflow
后台 MCP/Dashboard/CLI → workspace亲和准入 → Redis → cgi_slot_N → 独立Worker (solo, c=1)
未绑定只读Pipeline/UE及旧客户端 → cgi_queue → legacy cgi@%h
execution_strategy=control → 同一catalog/execution/receipt/audit → 短控制路径
自动workflow → cgi_queue准入控制 → 独立workflow队列/Worker → 成功后关闭自身
```

`project` 选择业务项目配置；可选 `workspace_project` 只选择工作区存储命名空间，省略时沿用 `project`。例如 `project=ysj, workspace_project=default, business_task_id=PL284` 使用 YSJ 规则，过程与结果位于 `projects/default/PL284`。工作区原生绑定、租约、恢复、任务闸均核对存储命名空间；业务项目另存于操作回执，不能借业务项目变化转移工作区。已有工作区不迁移。

注册 workflow 的 `runtime_lifecycle=temporary` 由现有 Pipeline worker 持久化准入和收尾，临时通道不计入三个驻留名额，不抢占或释放驻留场景。每个临时操作先预留 `CGI_WORKFLOW_MEMORY_GB`（默认 4 GiB），保留池的最低可用内存；不足时返回可查询、可取消的 WAITING，自动重试准入。实际内存使用仍取决于资产，并非无限机器资源。根操作锁继续保证同工作区串行。发送结果未知不重发，失败保留其自身场景与诊断；保存成功且文件存在后只释放该操作的 worker/DCC，最终回执包括 `workflow_runtime`。明确选择“不保存并继续编辑”的同步分支由 `resident_when` 使用原驻留方式；自动校验、完整构建和保存后的同步使用临时方式。

暂存优先保留源文件名和大小写；路径摘要只用于同名冲突副本。交付模板使用运行时校验的 `source_name`，不使用内部暂存别名。Maya 保存仍复用 `save_scene` 的目录锁和末尾版本递增；服务器原文件和已交付的旧文件不会自动改名。

使用驻留策略的后台任务按 `(workspace_project, workspace_id)` 固定运行通道；未提供 `workspace_project` 时沿用 `project`。驻留通道默认最多 3 个，低于 8 GiB 可用内存不新开，每通道预算 6 线程，均可配置。驻留满槽返回 `BLOCKED/RUNTIME_CAPACITY_BUSY/dispatched=false`，不会隐藏排队、抢占未保存场景或自动回收 dirty 后台；临时 workflow 使用上节的独立准入策略。同工作区以 broker 接受顺序串行；跨工作区可并行。Worker 内 Workflow 同步执行 API 子段，继承同一通道与根操作锁，不再次提交 Celery 子任务。前台调用选定的 DCC 会话桥；宿主内 `api.execute_local()` 直接调用统一执行核。这两条入口不进入后台队列，服务不接受 `execution_mode=local`，也不把非法策略静默改为后台。

`RuntimePool` 只保存资源归属，业务状态仍是原 workspace/operation/audit。receipt 回传 `runtime`（ID、generation、queue、root）；客户端不手选通道。每通道独立 PID/owner/锁、IPC、日志、TEMP 与 DCC 用户目录；源码、工具和项目配置保持原来源。非默认 pool 根附加稳定队列命名空间，避免共用 Redis 的验收服务与默认服务串队列。旧场景没有 runtime 归属时，`continue` 返回 `RUNTIME_LEGACY_BOUND`，不能隐式转移或重载；显式 `open` 才在新通道打开已保存源。

`pipeline.runtime.status` 和 `pipeline.runtime.release` 是目录登记的 `execution_strategy=control` 独立 API，仍经过同一个契约、执行核、receipt 和 audit。状态读取不需要空槽；释放在独立控制锁内先置 DRAINING，拒绝 pending/active，经所属通道查询 fresh dirty 后才关闭宿主及其 Worker。保存和释放分开；默认 dirty/UNKNOWN 拒绝，显式 discard 才允许丢弃。RPC 或停止结果未知时保留 DRAINING 占槽。故障恢复只停止 PID、出生时间、命令及 runtime 环境都匹配的进程；宿主 Popen 前意图未完成、身份不明或记录损坏都保留 UNKNOWN，不能从元数据缺失推断已清空。资源确认退出后保留 LOST 工作区，后续须显式 open。

根操作在原 operation state 写入执行尝试，并在原 operation 目录原子存储终态 result。晚确认重投恢复原 receipt；已有 RUNNING 且缺终态结果返回 NEEDS_ATTENTION，不能重做写操作。generation 或所属进程不匹配在 API 副作用前拒绝。该防重放不等于工作流业务回滚，也不自动取消迁移前的旧队列消息。

`list_apis` 和 `api_help` 返回同一个 `catalog_version`。调用端按该版本缓存已读合同；同版本重复调用直接执行。`execute_api` 和 `execute_batch` 在提交前比较 `expected_catalog_version`，版本过期时返回 `CATALOG_VERSION_STALE` 且不启动 DCC。

## 提交数据

公开 MCP 参数以工具 schema 和 `api_help.mcp_call_template` 为准：`execute_api` 的外层 `params` 包含 API ID、项目、工作区和执行策略；其中内层 `params` 才是业务参数。`task_id` 由服务生成。内部队列使用的 `api_params`、chain 节点的 `parameters` 不直接充当公开 MCP 调用示例。

Workflow 节点使用 canonical `api_id`；参数和规范操作模式由 `contracts`、执行由 `execution` 统一校验。`api.execute_local()`、服务入口及宿主 adapter 复用这一契约，业务 handler 接收 `(params, context)`。API ID 和具体参数只由 manifest 拥有，本文不另存一套。

`capability/operation` 描述功能，`stages/targets` 描述环节与对象，`execution_modes/surfaces` 描述执行入口。`access` 是整体读写等级；`effects` 具体声明条件性影响。`modes` 是 inspect/preview/apply 意图，多模式用 typed `mode_binding` 映射已有业务 selector。检查和预览仍可能写报告、临时编辑场景或切换宿主状态，不能用这些标签代替权限或无副作用保证。

### Workflow 组合与失败策略

可复用步骤定义放在 `workflows/fragments/*.json`，不会被 `list_workflows` 当作公开 workflow。公开 workflow 通过 `steps_include.fragment` 引用片段，并可用 `select` 选择步骤、`overrides` 覆盖已有步骤、`insert_after` 插入分支特有步骤。`parameters` 在同一 JSON 定义控制参数的类型、默认与枚举；`steps_include.variants` 及步骤 `when` 只支持这些控制参数的字面键值相等，多个键按 AND 匹配，不执行表达式或 Python。workflow 不能同时声明 `steps` 和 `steps_include`。

唯一纯编译 owner `compile_workflow(raw, extra_params)` 展开确定性分支，递归检查步骤参数、来源及修复参数中的 outputs 引用；生产者必须已经出现且已启用，`__failed__` 仅可出现在修复参数。列举从同一编译 owner 派生默认步骤、参数契约和调用模板；提交在绑定工作区前校验并生成 JSON-safe `_workflow_plan` 快照，执行端只分段消费快照，不重新读文件或编译。源解析、工作区来源恢复与业务输入仍归既有 owner，控制字段严格类型校验，未知字段拒绝。

编译、输入发现和执行共同使用 `core/workflow_templates.py` 的语法和递归求值。完整占位符保留 bool、数值、列表或对象类型；混合文本转为字符串。`outputs` 支持连字符步骤名、数字列表索引和既有带点端口优先，路径过滤及带引号的串联 `replace` 保留 Unicode。缺失输入、配置或输出字段返回 `WORKFLOW_TEMPLATE_INVALID`，链使用已有终态 `CHAIN_ERROR`，不把未解析文本传给 API，也不因此重放已完成调用。

分段准备只允许显式延后同段尚未产生的前驱输出；每步实际派发前仍严格求值。每段只使用首步来源初始化场景一次，后续来源声明会检查模板，不触发逐步重开。原生字面批次的参数和来源都不得含模板；需要前驱输出时由顺序执行路径求值。`iter_workflow_variants` 使用同一编译器枚举有限 bool/enum 控制域，治理检查覆盖所有合法分支及修复 API；只有显式 reject 可以跳过，其他错误和零合法分支都失败。

公开入口只有 `blender_to_maya_full_build(pack_textures=false)` 与 `tex_to_rig_verify_and_sync(mode=check, save_result=false)`。前者为 5/6 步，打包在提材质后、赋材质前；后者当前注册的 check 为 5 步（含层级预检/整理），geometry 为 9/10 步，geometry_materials 为 11/12 步，只有 save_result=true 增加最终保存。check + save_result=true 在工作区和 DCC 之前拒绝。机器结果与报告保留有效参数、保存请求、实际保存状态及保存路径；只从成功保存回执确认落盘，暂存文件和驻留场景修改不代表结果已保存。

步骤未声明 `failure_policy` 时默认 `abort`。显式策略只有四种：

- `abort`：保持原行为，步骤失败后中止当前链。
- `warn_continue`：记录 warning 并继续，链返回 `SUCCESS_WITH_WARNINGS`，workflow 返回 `WORKFLOW_SUCCESS_WITH_WARNINGS`。
- `needs_attention`：停止执行并返回 `CHAIN_NEEDS_ATTENTION`，workflow 映射为 `WORKFLOW_NEEDS_ATTENTION`。
- `repair_then_retry`：调用 `repair_step` 后只重试原步骤一次；回执保留 `initial_status`、修复状态、重试状态和是否恢复。修复或重试仍失败时按 `on_exhausted` 返回 `abort` 或 `needs_attention`。

`repair_then_retry` 当前固定 `max_attempts=1`，修复步骤必须提供 canonical `api_id`。失败步骤的机器输出可在修复参数中通过 `{{outputs.__failed__.*}}` 引用；自动修复必须是已登记的确定性 API，不能在 workflow 内嵌任意代码。

`failure_policy: null` 按未声明处理；其他非对象值拒绝。修复参数模板错误保留原失败 API 的回执并终止，不派发修复或重试。

## Receipt

每个 API 返回：

```json
{
  "api_id": "...",
  "api_version": "1.0.0",
  "status": "SUCCESS",
  "input": {},
  "output": {},
  "elapsed_sec": 0.0,
  "error_code": "",
  "error": "",
  "recovery_hint": "",
  "artifacts": []
}
```

失败时填写 `error_code`、`error`、`recovery_hint`；Workflow 只从上游 receipt 的 `output` 取机器数据。审计 JSONL 和 `REPORT.md` 是用户验收证据。

Manifest 的 `outputs` 是真实顶层字段到说明的映射，各模式的可选字段取并集；它不是每次必须全部出现的键集合。公共查询返回深拷贝，内部目录内容和版本保持同一快照。输入默认值与显式输入采用相同校验并独立复制；未知类型、非法默认值和类型不符的 choices 被拒绝。Receipt 的错误字段是字符串，`elapsed_sec` 为非 bool 的非负有限数，`artifacts` 为字符串列表；嵌套数据拒绝非 JSON 对象、非有限浮点数、非字符串对象键和循环引用。保留旧 handler 身份/版本向目录权威值归一的兼容行为。

MCP 出口依据共享 `task_status.is_failure` 标记业务失败，同时保留结构化回执。SDK client 返回这些业务失败回执；真正的工具异常和连接异常继续抛出。调用方检查业务 `status`，不能以 HTTP 成功、MCP 未抛错或存在文件替代业务验收。超时或断连后用原 `task_id` 恢复，不自动重提交已派发的操作。

## 工作区与场景

`task_id` 标识一次调用；`workspace_id + project` 恢复同一业务目录；`business_task_id + project` 自动归组到 `projects/{project}/{business_task_id}`。为兼容仍驻留的 Worker，新操作原生保存到 `.info/operations/{task_id}/`，其中 REPORT.md、state/result/manifest.json、报告锁、临时文件及附属报告按任务隔离；共享身份 `run_state.json`、来源登记和工作区／执行锁保持共享。根 `REPORT.md` 原子展示操作列表与最新提交操作的完整详情，操作报告发布后再刷新根视图，避免相反锁顺序。既有平铺 `.info/{task_id}_REPORT.md`、`{task_id}_{state,result,manifest}.json` 与目录布局记录均保留原址读取，不迁移、不双写，旧接管 commit witness 和原始回执无需更新；同一任务 ID 的双布局或身份冲突拒绝恢复，不默默切换来源。全局 audit JSONL、64 KiB 内联预算及 `receipt_ref.entry_index/result_index` 保持原定义。

普通 UE 现有会话、只读 Pipeline API 及其同运行时 batch，没有显式工作区、来源或 `scene_action=open` 时仅保留审计。写文件的 Pipeline API 走工作区分配。分流由服务读取目录契约决定，不能由调用方传跳过标记。

Maya/Blender 后台的 `scene_action=continue` 首次将来源 staging 到工作区并打开；之后复用同一 live owner，保留未保存修改，不再定位、复制、打开或重放 API。完整调用集合均为 Maya/Blender inspect/preview、又未选来源或既有工作区时，返回 `SCENE_TARGET_REQUIRED`；显式 `scene_action=open` 可选择空的隔离场景。apply 和既有 Pipeline→DCC 组合保留初始化路径。这是保守的初始化政策，不能当作精确的场景依赖分析。

owner 被替换、进程退出或初始化失败时，continue 返回 `WORKSPACE_SCENE_UNAVAILABLE`、`RUNTIME_LOST` 或具体 generation 错误。恢复须明确 `scene_action=open`，并指向实际保存文件；空来源意味着新场景。不会自动重放已执行动作，也不提供失败 workflow 的公开断点续跑。每个通道的 Maya/Blender warm pool 各保留一个后台场景，不同工作区拥有不同通道；工作区目录存在不证明内存仍在。

显式 `open` 总是重新读取原生源文件，包括与当前场景同一路径的情况。专用开场景操作同时确认传输、exec 回执、操作身份、宿主及实际场景路径，失败不记为 READY；通用 `exec_code` 的任意嵌套业务状态不被全局提升。空来源或 ABC/FBX 等非原生来源初始化空场景，后续导入仍由相应 API 完成。

前台默认操作选定会话的现有场景；显式 workspace_id 只关联报告，不把后台场景搬到前台。前台不支持 `scene_action=open`；实际开场景由对应 API 执行。前台必须给显式端口并满足该宿主桥契约；`execute_batch` 当前仅支持 Maya 前台批次，不能从单 API 的 foreground 标签推断其他宿主也支持前台 batch。

## 保存与写入范围

清理、修改和保存是独立能力。`maya_master_cleanup` 的 check/fix 返回诊断或清理结果，不提供 `save_path` 参数，不自动保存，也不把 Markdown 报告写到场景旁；check 可能改变当前 UV 集。需要持久化时由调用方或 workflow 显式组合保存，并检查前序步骤成功。没有保存步骤的检查或更新仍是合法组合。

Maya/Blender 后台场景处理遵循 staging；前台已授权增量工作可操作用户指定的实际工程。实际写入还受该 API 自身契约限制：例如 `maya.asset.save_scene` 对当前场景和目标检查 managed `projects/`、`runs/` 根及 protected path，并自动选择下一个版本；它当前没有按 `context.run_dir` 实施跨工作区权限隔离，前台调用它也受此限制。

`maya.asset.save_scene` 与 `blender.asset.save_scene` 共用 `core/scene_files.py` 的版本命名与真实 OS `.version_lock`；锁失败或超时拒绝保存，无无锁回退。保存结果以实际 `output.output_path` 为准，当前宿主文件名会随保存改变。Blender 只有在 `FINISHED`、非空文件、当前路径匹配与 `is_saved` 全部成立时报告 `save_completed=true`、`artifact_verified=true`。原始 `native_dirty` 如实保留；后台模式的 `clean_state=UNKNOWN`，保存 `SUCCESS` 只证明保存动作及产物。交互模式确认 dirty 时仍返回 `NEEDS_ATTENTION`。

后台 Blender 的宿主循环不消费窗口管理器的保存完成通知，不能用 `is_dirty` 证明场景可安全关闭。release 的新鲜探针读取实际运行模式；后台或模式未知时默认拒绝释放，只有独立明确的 `discard=true` 请求才可放弃所属驻留状态。即使显式 discard，仍须满足所属代次、进程身份与无 pending/active 操作。保存成功不得自动转换成 discard 授权；不强清原生 dirty、不重开当前场景、不维护另一套场景状态镜像。

`path_guard` 拦截配置的只读盘、发布源和 UNC 保护路径；它是协作检查，不是任意 Python 的操作系统隔离。不能将后台 staging 扩大为所有入口都只写当前工作区，也不能将前台授权扩大为绕过 API 的专有保存限制。

## 公共实现与运行时更新

后台 worker 和 adapter 组合共享 `hosts.file_ipc.FileIpcTransport`，文件在项目根 `ipc/`；部署根沿用 `CGI_PROJECT_ROOT`。该类只拥有本 worker 的原子收发、轮询、结果相关性校验和清理。启动、超时、进程退出与 kill 留在宿主 worker，Maya 前台 RPyC 不使用后台文件 IPC。warm proxy 的普通退出不关闭 resident，真实回收才清理其所属目录。

Maya、Blender 的单次和批次等待均检查所属宿主进程是否退出；宿主死亡会结束等待并保留失败结果。非正 `IPC_TIMEOUT_SEC` 对仍存活的宿主不设置时间上限，正超时继续使用原宿主回收策略。

目录发布变化可自动刷新 manifest 投影，`catalog_version` 不覆盖 MCP schema、核心模块或会话有效期。修改能力实现须提高对应 manifest 版本并生成目录；修改框架须分别刷新前端连接和后台 Worker，旧 STDIO 客户端需要重连。只更新文件、看到新目录版本或服务健康，都不足以证明核心已加载。

服务仍由 `cgi_pipeline.core.service_manager` 管理，共享 Worker 常驻。HTTP manager 只管理其前端；MCP 断开不停止 Worker。Worker restart 会影响后台 warm 场景，须先核 active/reserved/scheduled、broker ready 与已知任务状态，保留无关工作；solo 忙时 inspect 超时表示未知，不能当作空闲。更新后用新进程/连接和代表性实际回执验证，不自动停止整套运行时。
