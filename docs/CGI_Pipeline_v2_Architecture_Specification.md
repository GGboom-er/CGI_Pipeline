# CGI Pipeline v2 架构与设计细则规范 (Architecture & Specification)

这份文档汇总了 CGI Pipeline v2 在经过重构后的核心运行架构、设计规则以及强制规范。任何后续对该管线的开发、维护和技能（Skill）扩展，必须**无条件遵循**本规范，以此保证系统的健壮性、可追溯性和零幻觉执行。

---

## 1. 核心架构设计 (Architecture)

### 1.1 无限期长连接与资源管理 (No Hard Timeouts)
- **取消硬超时**：摒弃了早期 Celery 的硬性超时机制（`time_limit` / `soft_time_limit` 被彻底移除）。对于大型资产（几百万面、超大缓存），允许无限制运行，确保运算不被半途腰斩导致文件损坏。
- **温水池机制 (Warm Worker Proxy)**：DCC 进程 (Maya/Blender) 由 `dcc_factory.py` 以后台进程形式拉起。对于单次链式执行，生命周期为 `创建 -> 执行技能链 -> worker.shutdown() -> 退出进程`，避免内存泄露污染后续任务。

### 1.2 Fail-Fast 防御层与输入强校验
- **Schema 边界卡控**：任务进入管线后、触达 DCC API 前，必须经过 `core.schemas.SkillPayloadSchema` 严格验证。
- **拒绝脏数据**：`extra='forbid'` 级拦截。只要传入了对应 `SKILL.md` 中未定义的多余参数，或者必填项缺失，立刻被 `ValidationError` 拒之门外，绝不允许因为少传一个 ID 导致 Maya 后台在跑了半小时后才报错。

### 1.3 异常业务级脱水 (Error Dehydration)
- **屏蔽原生黑盒**：美术用户或调度平台不需要看 `IndexError` 或 `RuntimeError`。
- **异常转译**：管线统一使用 `_translate_error` 拦截器。所有原生系统报错（如 `FileNotFoundError`）会被转化为清晰的业务级大白话，如 `[文件读取异常] 尝试读取的源文件不存在或被占用...`，并附带针对性的恢复建议（Recovery Hint）。

### 1.4 路径沙盒与保护 (Path Guard)
- **绝对只读（第一性原则）**：原始生产环境（如 `X:/Project/ysj/pub/...`）默认被列为受保护路径，**绝对不允许在 X 盘上进行任何增删改操作（写入、覆盖、重命名、删除）**。X 盘路径只能用于**拷贝**和**读取**。
- **强制沙盒流转**：管线在执行任何任务时，底层引擎 (`core.tasks.py`) 侦测到 X 盘来源后，会自动在项目本地的临时任务区（如 `projects/{project}/{date}_{asset}_{task_id}/`）创建专属沙盒目录，并将所有所需的源文件（`.ma`, `.blend`, `.json` 等）拷贝进去。
- **沙盒内闭环计算**：所有的技能处理、生成的新文件、输出的报告全部落在本地任务沙盒目录内。绝对杜绝任何对生产环境的直接修改。直到人工确认后才通过发布流程回写到 X 盘。

---

## 2. 技能开发规范 (Skill Convention)

所有可执行的管线节点均被称为 **Skill**。

### 2.1 目录结构
必须保持完全的自闭环文件夹结构，以利于自动化发现与分发：
```text
skills/{skill_name}/
├── {skill_name}.py       # 技能的主入口（必须包含 execute(payload) 函数）
├── SKILL.md              # 技能注册与文档（包含 YAML frontmatter）
└── __init__.py           # 用于保证相对导入
```

### 2.2 SKILL.md 编写四段式法则
每个技能文件夹下必须有一个 `SKILL.md`，它既是管线自动解析配置的来源，也是开发者和 AI 调用的参考指南：

1. **YAML Frontmatter (头部)**：必须包含 `skill_id`, `name`, `dcc` (如 maya/blender)。
2. **🔴 核心限制 (CRITICAL CONSTRAINTS)**：列出所有的防线、不能做的操作、仅支持的边界情况。
3. **🟢 核心功能 (CORE FUNCTION)**：陈述该技能的具体目的、工作流流程。
4. **🟡 参数规则 (PARAMETERS)**：详细规定 `execute` 入参 `payload` 中需要哪些字段，字段必须带有明确的默认值和类型，用于前端生成面板。

---

## 3. 任务报告与拼装规范 (Task Report Specification)

整个系统采用**“化零为整的 Markdown 追加流”**。用户仅需最终查看一份总览 `REPORT.md`，即可做到**无需查日志、一眼定生死**。

### 3.1 Receipt (执行收据)
所有 Skill 的 `execute()` 返回值必须是 `core.receipt.make_receipt` 生成的标准化收据字典：
```python
return make_receipt(
    skill_id='maya_master_cleanup',
    status='SUCCESS',           # 或 ERROR / NEEDS_ATTENTION
    start_time=t0,
    summary_input=scene_name,
    summary_action='场景清理',
    outputs={'report_path': md_path}, 
    report_content=report_md_str  # [强制] 注入的详细 Markdown
)
```

### 3.2 详细信息注入 (`report_content`)
- **注入机制**：如果某技能进行了复杂的分析或大量清理（如 `compare_asset`, `master_cleanup`），该技能需将自带的详细 Markdown 内容传递给 `receipt['report_content']`。
- **管线装配**：管线核心的 `append_step` 引擎会自动将该 `report_content` 原封不动地内嵌到主报告的 `<details><summary>📋 详细报告</summary>` 中，供用户核对（对账单式体验）。

### 3.3 大数据量防爆机制 (Max Items Limit)
- **输出截断**：由于场景中可能有几万个模型或垃圾节点，任何通过 MD 生成的数组或列表输出，**必须强制设定最多显示数量（目前定为 `MAX_DETAIL_ITEMS = 20`）**。
- **行为要求**：对于被清除的未知节点、野生相机、空组等，列出前 20 项供用户复查，超出的部分统一追加 `- *...及其他 N 个*`，以防止最终的报告 Markdown 被撑爆导致 UI 渲染卡死。

---

## 4. 落地与校验要求 (Verification)

1. **不能为了跑通而凑活 (No Hacky)**：任何 API 的异常不得简单使用 `try...pass`，必须 `catch` 后转化为有意义的日志或转译抛出。
2. **只读任务零残留**：像对比分析（compare）或构建指纹（build_info）这种纯读取动作，绝对不可调用 `save` API，绝对不可向场景插入临时节点，必须在查询后直接退出。
3. **中文文档强制**：一切 `SKILL.md` 和最终的 Markdown 报告提示，必须保证 100% 中文可读，不可抛给用户原生的 Python 错误栈。

---
*本文档为 Pipeline 最终版本结构契约，各节点开发者与调度分配器需严格遵守。*
