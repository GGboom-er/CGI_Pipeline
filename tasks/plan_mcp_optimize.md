# CGI MCP 工具优化落地计划

> 目标：让 AI 有对的工具就用、不掉进手搓，使 MCP 更精简/高效/直接。不删任何能力，靠"补全默认路径 + 定死工具角色"实现。

## 根因（本会话坐实）
- 病不在工具多，在**默认路径不完整 + 工具角色没定义**：正规路径一有摩擦，AI 就掉进 `exec_code` 手搓 → 失控。
- 症状1：`pipeline_execute_workflow`(tools_operations.py:452) 发射后不管，只回 task_id → AI 手写开 worker/轮询/关。
- 症状2：exec_code 定位不清 → AI 拿它在工作流外搭编排（它其实是"工作流里的特殊步"积木）。
- 症状3：前台连 Maya 契约不清 → AI 手搓 socket，不用 foreground 工具。

## 研究背书（三目标 → 四原则）
- Anthropic《Writing effective tools》+ Itential《Don't become a mess》共识："一个工具要调 5 个才出结果，就是抽象层级错了"。
- 原则1 Outcome 优先（更直接）｜原则2 角色清晰而非删除（更精简决策面）｜原则3 错误能自救（更高效）｜原则4 响应高信号（更省 token）。

## 目标形态
```
生产：pipeline_execute_workflow(wf, project, asset, fresh, wait, shutdown_after)
      → 一次调用跑完 → 12 步 ✓/✗ 清单 + report_path + worker 已关
调试：execute_skill(skill_id, execution_mode='foreground', foreground_port=<端口>)
      → skill 在你开着的 Maya 里本地跑
特殊步：exec_code 作为工作流里的一步，补没有专属 skill 的一次性步
```

## 改动文件与阶段

### 期1 · wait + 步骤回显（最小、纯读、不碰 worker）
- `mcp_server/models.py` `ExecuteWorkflowInput`(185)：加 `wait/wait_timeout_sec=1800/poll_interval_sec=5`。
- `mcp_server/tools_operations.py` `execute_workflow_tool`(462)：提交后 `asyncio.sleep`+`asyncio.to_thread(_read_audit)` 轮询到终态（`core.task_status.is_terminal`）才返回。
- 终态从审计 entries 提取每步 `STEP_START/STEP_SUCCESS/STEP_ERROR` → 逐条 ✓/✗ 清单 + report_path。
- 超时诚实返回 PROGRESS + task_id + 续查提示，不误判。
- 验证：真机跑一个轻量工作流，确认阻塞到终态 + 步骤清单正确。

### 期2 · fresh + shutdown_after（碰 worker 生命周期）
- `mcp_server/internals.py` 抽 `_workflow_worker_dccs(workflow_id)`：{workflow} ∪ 各步 DCC（复用现有 _submit_workflow:201 扫描逻辑）。
- `ExecuteWorkflowInput` 加 `fresh=False/shutdown_after=False`。
- `execute_workflow_tool`：fresh→提交前 restart 相关 worker；终态后 shutdown_after→stop。超时不 stop（任务还在跑）。
- 验证：真机验 restart→wait→stop 三段，确认不误杀在跑 worker。

### 期3 · 错误自救 + 响应精简（原则3/4）
- 所有操作工具失败返回统一带 `recovery_hint`=下一步该调的工具/动作（部分已有，补齐）。
- 响应统一 `status + 一句结果 +(步骤清单|recovery_hint)+ 引用路径`；大 payload 截断（推广 _read_audit:333 的终态瘦身）。
- 验证：正例/异常/边界各测，确认无噪声灌 context。

### 期4 · 契约文案（原则2 · 纯文档）
- `pipeline_execute_workflow`/`execute_skill`/`exec_code` 描述写清四类角色定位（生产/调试/特殊步/观测）。
- 同步 SKILL.md、AGENTS/README 相关段。

## 保持不变
- 命名空间前缀 `pipeline_`/`maya_`/`blender_` ✓
- `destructiveHint` 等注解 ✓
- 工作流引擎中间结果走 Redis/文件不进 context ✓
- 所有 skill + exec_code 全保留（是工具箱 + 调试 + 特殊步）
- 前台"必须显式传端口"的多实例安全门 ✓（不改）

## 验证矩阵（每期改完必跑）
| 层 | 内容 |
|---|---|
| 单元 | 若 tests harness 在，补/调工作流托管契约测试 |
| 真机 | 轻量工作流验 wait→步骤回显→fresh→shutdown 四段 |
| 隔离 | 后台工作流 + 前台 skill 调试同时跑，确认不打架 |
| 回归 | dashboard 启停、cli.py run-skill 仍正常（底层函数未动） |

## 风险 / 待确认
1. wait 超长工作流可能触发 MCP 客户端超时：服务端不受影响，回退 `maya_query_task` 续查——超时分支已给提示。
2. `fresh=true` 会强杀在跑 worker（符合"一任务一重开"，文档写明，勿并发用）。
3. 默认全 False：CLI 与现有调用零影响。
4. 分期可回滚：期1 纯读独立可测，逐期真机验证再进下一期。

## 执行顺序
期1(wait+回显+真机) → 期2(fresh/shutdown+真机) → 期3(错误/格式) → 期4(文案) → 全链回归。
每期动手前把该期逻辑与用户对齐，拍板才改；改完 reload_server + 真机验证再勾 todo。
