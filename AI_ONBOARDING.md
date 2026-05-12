# 🤖 CGI Pipeline AI 快速上手与防御指南 (AI Onboarding Guide)

**致任何新接入本项目的 AI Agent（例如重置了上下文的 Claude、Gemini 或其他模型）**：
如果你刚刚进入这个项目，请**务必仔细阅读本指南的每一句话**。本项目是一个高度抽象、完全数据驱动且对数据安全要求极高的 CGI Pipeline 自动化管线。如果你按传统的“随便写写单体脚本”的经验来这里写代码，你**一定会**破坏系统甚至摧毁生产数据。

---

## 🛑 绝对不容突破的红线 (CRITICAL RED LINES)

1. **绝对禁止吞没异常 (NO Silencing Exceptions)**
   - 错误范例：`try: cmds.setAttr(...) except: pass`
   - **正确做法**：如果必定会出错且可以忽略，必须加上业务解释；如果是严重异常，必须转译抛出或通过 `make_receipt(status='ERROR')` 向上级汇报。决不能让 Maya/Blender 在后台处于未决的崩溃边缘。
   - 参考：所有的异常都应通过 `core.receipt` 和 `_translate_error` 机制进行业务脱水，把报错变得对人类可读。

2. **绝对禁止暴力读写 (NO Native Save Without Guard)**
   - 任何 `cmds.file(save=True)`，或者是任何通过 `os`, `shutil` 将文件写入生产服务器盘符（比如 `X:/Project/`）的行为都是危险的。
   - **正确做法**：写入操作必须落在当前任务沙盒（`projects/{project}/{timestamp}_{asset}_{task_id}/` 或 `runs/{task_id}/`）。发布到生产目录只能由后续专用发布技能处理；普通技能不得要求人工选择后继续。

3. **任务通讯只能用 `receipt` (Strict Receipt Contract)**
   - DCC 里的执行结果和日志，绝对不可以通过直接写 `.txt` 或者 `print()` 来通知用户。
   - **正确做法**：所有 `skills/{skill_id}/{skill_id}.py` 的 `execute(payload)` 函数，必须只返回 `from core.receipt import make_receipt` 生成的标准字典。这是 Dashboard 和主控程序唯一认准的通行证。

4. **没有硬超时 (NO Hard Timeouts)**
   - 我们运行的是千万面级别的模型和几个 G 的 ABC 缓存，**不要擅自**在代码里加入任何 `timeout=60` 等强制腰斩的代码。
   - **正确做法**：使用我们封装好的 `internals._submit_to_celery`，通过 `task_id` 无限期轮询状态。

5. **数据量防爆截断 (Max Items Limit)**
   - 在生成报告或罗列失效节点时，如果超过 20 条，必须截断！（使用 `...及其他 N 个`）。
   - 我们吃过 UI 被几万个节点字符串撑爆卡死的亏，请牢记。

---

## 🗺️ 系统架构速览 (Architecture Overview)

- **双 MCP 调度层 (MCP Servers)**：位于 `mcp_server/`。这是你目前连接的地方。系统暴露了两个 MCP 服务：
  - **`cgi-pipeline` (管线调度)**：实现了**全自动技能发现机制**。所有 `skills/` 下的合规技能，会在运行时自动生成强类型的 Pydantic Input Model 并作为独立的 MCP Tool 暴露。不论是后台还是前台任务均经由它分发并落盘审计（Audit）。
  - **`maya-live` (实时交互)**：提供纯代码直连，通过 `CommandPort` 暴露极速 REPL 能力，用于临时测试代码或获取场景状态。
- **业务层 (Skills)**：位于 `skills/`。它是管线的核心资产库。所有的业务逻辑被彻底拆解成单个功能的文件夹（如 `maya_clean_skinweights`）。
- **执行层 (Workers)**：后台的 Maya 和 Blender 以后台常驻进程（Warm Workers）形式通过 Celery 管理。前台执行则通过 Socket 直连。
- **配置层 (Config & Registry)**：位于 `config/` 和 `core/skill_registry.py`。项目相关的路径全靠 `load_project_config(project)` 解析，切勿硬编码。

---

## 🎮 前台无感连接优先原则 (Foreground-First Strategy)

当用户要求你“诊断场景”、“查看状态”、“修改当前文件”或“执行一段测试代码”时：
**请默认在 MCP 工具的参数中使用 `execution_mode: "foreground"`。**
- 本项目内置了“零配置 (Zero-Config)”无感通信通道，底层会自动嗅探并连接活跃的 Maya/Blender 前台端口（如 7001-7010），你**不需要**关心底层通信和端口配置。
- 除非用户明确要求“跑大批量处理任务”或指定使用 `background`，否则请优先使用前台模式。这不但响应极快，还能让用户在自己的软件界面里直观地看到你做的任何修改。

---

## 🛠️ 当用户要求“写一个新功能”时怎么做？

如果你收到了开发新技能的任务，**不要立刻写代码！**
请立即阅读并遵守 Meta-Skill 协议：
**读取 `skills/build_pipeline_skill/SKILL.md` 并进入“技能构建向导模式”。**

---
> *AI, 请证明你已阅读此文件。在此后的所有对话中，当你需要设计逻辑时，请时刻在 <thought> 标签内回想本指南的红线。*
