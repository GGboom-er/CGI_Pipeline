# 运行时任务报告规范

本文档定义 CGI Pipeline 的唯一任务报告机制。它补齐运行时报告与现有 `receipt` / `audit` / `write_task_report` 之间的职责边界。

## 1. 目标

每个任务只能有一份用户可读 Markdown 报告：

```text
{sandbox}/REPORT.md
```

任务成功、失败、阻断、审计失败都必须进入同一份报告。报告必须能回答：

- 任务从哪里读取文件
- 哪些文件被复制到沙盒
- 每个路径节点的输入、输出、状态和耗时
- 每个 skill 收到什么参数
- 每个 skill 输出什么结果
- 每个 skill 是否成功
- 每个 skill 耗时多少
- 出错时完整错误和恢复建议是什么

机器中间产物仍统一写入 `{sandbox}/.info/`，报告不承担下游数据传递职责。

## 2. 职责边界

### 2.1 业务 skill

业务 skill 只生产事实，不决定报告文件形式。

允许返回：

- `receipt.summary`
- `receipt.items`
- `receipt.outputs`
- `receipt.error`
- `receipt.recovery_hint`
- `receipt.report_content`
- `receipt.report_sections`

禁止：

- 自行写零散 Markdown 报告
- 把 Markdown 当作下游机器数据
- 把统计字段塞进 `outputs`
- 同时输出 `report_sections` 后仍让旧 `report_content` 重复展示

### 2.2 实时报告 writer

实时报告由调度层调用：

```text
core/task_report_writer.py
```

它负责：

- 初始化 `REPORT.md`
- 写入源文件与沙盒备份模块
- step 开始时写入 `RUNNING`
- step 完成时替换为 `SUCCESS` / `ERROR` / `BLOCKED` / `AUDIT_FAILED`
- workflow / chain 结束时写入最终状态

### 2.3 write_task_report

`skills/write_task_report` 保留，但定位是：

- 从 audit 重建报告
- 灾后恢复
- 最终重渲染兼容旧任务

它不是运行中报告系统，不参与每个 skill 的实时更新。

## 3. 模块更新方式

报告中的每个模块必须用 block marker 包裹：

```md
[//]: # (report:block:start step:main:0:maya_compare_asset_in_scene)
### Step 1/1 | 对比资产 | SUCCESS | 2.4s

...
[//]: # (report:block:end step:main:0:maya_compare_asset_in_scene)
```

同一个 block id 再写入时必须替换原模块，不能追加重复内容。

marker 使用 Markdown reference comment 形式，避免在普通 Markdown 查看器里显示 HTML 注释。

## 4. 默认排版规则

报告使用普通 Markdown 标题、表格和 fenced code block，不依赖 `<details>`。

每个运行节点必须优先展示：

- 节点
- 参数
- 输入
- 输出
- 状态
- 耗时

复杂明细放在本 step 下的 `####` 小节，列表超过 20 条必须截断。

## 5. 结构化详细信息

复杂 skill 优先返回 `report_sections`：

```python
report_sections=[
    {
        "title": "通过配对",
        "summary": "73 项",
        "items": [{"name": "...", "detail": "..."}],
    },
    {
        "title": "几何差异",
        "summary": "0 项",
        "items": [],
    },
]
```

旧技能继续使用 `report_content`。当同一 receipt 已经提供 `report_sections` 时，实时 writer 只渲染 `report_sections`，不再重复渲染旧 `report_content`。

## 6. 沙盒与命名

沙盒目录使用：

```text
projects/{project}/{YYYYMMDD_HHMMSS}_{asset_name}
```

同一秒同资产重复创建时追加：

```text
_01
_02
```

`task_id` 只写入 `REPORT.md`、`manifest.json`、audit 和 `.info/run_state.json`，不进入用户主要识别目录名。
