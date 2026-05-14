# 运行时任务报告规范

本文档定义 CGI Pipeline 的唯一任务报告机制。skill 输出字段以 `skills/build_pipeline_skill/SKILL.md` 为准；本文只规定 `REPORT.md` 如何渲染。

## 1. 目标

每个任务只能有一份用户可读 Markdown 报告：

```text
{sandbox}/REPORT.md
```

任务成功、失败、阻断、审计失败都必须进入同一份报告。报告只回答五件事：

- 执行了什么 skill
- 是否成功
- 耗时多少
- 展开后有哪些核心结果明细
- 最终产物在哪里

机器中间产物统一写入 `{sandbox}/.info/`。报告不承担下游数据传递职责。

## 2. 数据来源

报告只消费每个 step 的标准执行记录。记录字段和新增 skill 输出规则以 `skills/build_pipeline_skill/SKILL.md` 为准，本文不重复定义。

`REPORT.md` 不渲染独立 `Input` / `Output` 章节。可见明细来自标准记录的 `output`，并显示在 step 的 `Details` 中。

## 3. 职责边界

### 3.1 业务 skill

业务 skill 只负责返回标准执行记录；具体字段、旧字段兼容边界、禁止事项均以 `skills/build_pipeline_skill/SKILL.md` 为准。

### 3.2 实时报告 writer

实时报告由调度层调用：

```text
core/task_report_writer.py
```

它负责：

- 初始化 `REPORT.md`
- step 开始时写入 `RUNNING`
- step 完成时替换为最终标准执行记录
- workflow / chain 结束时写入最终状态
- 只渲染 step 折叠头、`Details` 和必要错误信息

### 3.3 write_task_report

`skills/write_task_report` 保留，但定位是：

- 从 audit 重建报告
- 灾后恢复
- 兼容旧任务的最终重渲染

它不是业务 skill 输出规范的第二套来源。

## 4. 模块更新方式

报告中的每个模块必须用 block marker 包裹：

```md
[//]: # (report:block:start step:main:0:maya_compare_asset_in_scene)
### Step 1 | maya_compare_asset_in_scene | SUCCESS | 2.4s

...
[//]: # (report:block:end step:main:0:maya_compare_asset_in_scene)
```

同一个 block id 再写入时必须替换原模块，不能追加重复内容。

## 5. 默认排版规则

每个 step 固定排版：

```html
<details>
<summary>Step N/Total | {skill} | {status} | {elapsed}</summary>

<h4>Details</h4>
...

</details>
```

规则：

- 报告打开时只露出每个 step 的折叠头。
- 不渲染独立 `Input` / `Output` 标题。
- `output` 中的标量进入 `Details/result` 表。
- `output` 中的数组或分组对象按字段名展开为明细表或列表。
- `output.compare_result` 这类大型机器对象不直接展开；报告只显示同级统计字段。
- 报告层不生成业务结论，不从旧 Markdown 中反推统计。

## 6. 对比类报告

对比类字段由 `skills/build_pipeline_skill/SKILL.md` 定义。报告层只负责把标准记录 `output` 中已有的统计和 `*_items` 明细渲染到 `Details`，不从 `compare_result.json` 反推展示字段。

## 7. 沙盒与命名

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
