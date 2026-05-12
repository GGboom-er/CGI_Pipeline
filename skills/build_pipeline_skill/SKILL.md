---
skill_id: "build_pipeline_skill"
name: "技能构建向导"
dcc: "pipeline"
description: "Meta-Skill，专门用于引导大模型（AI）自动化、规范化地创建 CGI Pipeline 的新技能。"
parameters: {}
io:
  inputs: []
  outputs:
    - name: "protocol"
      type: "report"
      label: "构建协议"
category: "system"
---

# 🤖 [AI 专属向导] 技能构建协议 (Skill Builder Protocol)

> [!IMPORTANT]
> **致 AI 代理（包括 Claude, Gemini 等）**：
> 当用户明确要求“创建新技能”或“写一个新功能放入管线”时，你必须**立即停止直接输出代码**，并严格遵守本指南进入【技能构建向导模式】。

本技能是一个交互式的 Meta-Skill，旨在确保你写出的代码 100% 符合 `CGI Pipeline v2` 架构与 `CONVENTION.md` 规范。

---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
这是 AI 构建协议，不负责直接修改 DCC 场景。用户要求创建新技能时，应先输出蓝图并等待确认。

### 🟢 核心功能 (CORE FUNCTION)
引导 AI 按 CGI Pipeline v2 规范创建新技能，覆盖意图捕获、蓝图、代码生成和交付检查。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
执行文件仅返回协议说明 receipt；真正的脚手架生成应由 AI 按本文件约束创建 `skills/{skill_id}/`。

### 🟡 参数规则 (PARAMETERS)
无运行时参数。

## 阶段一：意图捕获 (Discovery Phase)

向用户发送一条清晰的消息，要求确认以下必填项（如果用户尚未提供）：
1. **技能 ID (Skill ID)**：如 `maya_clean_skinweights`。
2. **目标执行环境 (DCC)**：`maya` / `blender` / `pipeline`（纯系统级）？
3. **核心功能与危险等级**：该技能会不会破坏文件？是否需要强制存入任务沙盒？
4. **所需参数 (Parameters)**：需要用户在面板上填入什么变量？（例如阈值、开关、特定节点名字等）。

*（在用户给出明确答复前，不得进入下一阶段！）*

---

## 阶段二：输出技能蓝图 (Blueprinting Phase)

根据收集到的信息，严格按照以下“四段式”结构向用户展示 `SKILL.md` 的初稿供其审核：

```markdown
---
skill_id: "{skill_id}"
name: "描述"
dcc: "{dcc}"
category: "process"
description: "{用一两句话简明扼要地描述}"
parameters:
  param_name:
    type: "string"
    description: "说明"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- 列出防线，例如：不可逆操作、禁止使用硬编码路径、需在 undo chunk 内。

### 🟢 核心逻辑 (CORE LOGIC)
- 说明代码如何运作。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
- 列出关键的依赖模块或代码片段。

### 🟡 参数规则 (PARAMETERS)
- `param_name` (type): 说明。默认值。
```

*（必须询问用户：“这份架构设计是否符合您的预期？可以开始生成代码了吗？”）*

---

## 阶段三：严格生成代码 (Execution Phase)

一旦用户批准，生成代码必须严格遵守以下契约，**绝不允许犯这些常见错误**：

1. **入口函数**：必须且只能暴露 `def execute(payload: dict) -> dict:`。
2. **标准收据**：必须引用 `from core.receipt import make_receipt`，并在结尾返回该字典。
3. **异常处理**：
   - 绝不允许 `try: ... except Exception: pass`！
   - 发生错误必须通过 `make_receipt(status='ERROR', error="明确的业务级报错文字")` 返回。
4. **日志与信息爆炸防范**：
   - 如果技能需要在 `receipt` 中注入 `report_content`（Markdown格式的长列表），必须强制进行截断。
   - 规则：`MAX_DETAIL_ITEMS = 20`，如果超过 20 条，追加一句 `- *...及其他 N 个*`。
5. **路径安全**：
   - 获取项目配置必须使用 `from core.config_loader import load_project_config`。
   - 不要试图在 Maya 中直接 `cmds.file(save=True)` 覆盖源文件；输出只能落在当前任务沙盒，非法路径必须返回 `BLOCKED`。
   - 后台技能不得设计成人工暂停后继续；数据契约或 QC 不通过时返回 `AUDIT_FAILED` 并给出 `recovery_hint`。

### 代码模板参考：
```python
import time
import traceback
from core.receipt import make_receipt

def execute(payload: dict) -> dict:
    t0 = time.time()
    try:
        project = payload.get('project')
        params = payload.get('parameters', {})
        
        # ... 业务逻辑 ...
        
        return make_receipt(
            skill_id='{skill_id}',
            status='SUCCESS',
            start_time=t0,
            summary_input='输入对象',
            summary_action='执行了什么',
            summary_count=1,
            summary_label='项',
            outputs={}  # 若产出文件，使用 'output_path'
        )
    except Exception as e:
        return make_receipt(
            skill_id='{skill_id}',
            status='ERROR',
            start_time=t0,
            error=f"执行失败: {str(e)}\n{traceback.format_exc()}"
        )
```

---

## 阶段四：打包与交付 (Packaging Phase)

1. 使用 `write_to_file` 工具创建以下文件：
   - `skills/{skill_id}/SKILL.md`
   - `skills/{skill_id}/{skill_id}.py`
   - `skills/{skill_id}/__init__.py` (内容: `from .{skill_id} import execute`)
2. 向用户发送交付报告，并提醒用户在 Dashboard 或调用链中进行首次测试。
