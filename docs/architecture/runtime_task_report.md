# 运行时任务报告规范

本文档定义 CGI Pipeline 的唯一任务报告机制。skill 输出字段以 `skills/build_pipeline_skill/SKILL.md` 为准；本文只规定 `REPORT.md` 如何渲染。

## 1. 目标

每个任务只能有一份用户可读 Markdown 报告：

```text
{sandbox}/REPORT.md
```

任务成功、失败、阻断、审计失败都必须进入同一份报告。报告只回答五件事：

- 执行了什么 skill
- 传入了什么参数
- 输出了什么内容
- 是否成功
- 耗时多少

机器中间产物统一写入 `{sandbox}/.info/`。报告不承担下游数据传递职责。

## 2. 数据来源

报告只消费每个 step 的标准执行记录：

```json
{
  "skill": "save_scene",
  "input": {
    "source_path": "Y:/.../ysj_chr_maYouA_rig_rigMaster_v001.ma"
  },
  "output": {
    "output_path": "Y:/.../ysj_chr_maYouA_rig_rigMaster_v002.ma"
  },
  "status": "SUCCESS",
  "elapsed_sec": 0.5
}
```

业务 skill 不再为报告返回 `summary`、`items`、`report_content`、`report_sections`、`recovery_hint`。

## 3. 职责边界

### 3.1 业务 skill

业务 skill 只负责返回标准执行记录：

- `input`: 本次实际生效的参数，路径必须是完整路径
- `output`: 本次实际产物，文件产物必须使用 `output_path`
- `status`: 执行状态
- `elapsed_sec`: 执行耗时

业务 skill 禁止：

- 自行写零散 Markdown 报告
- 把 Markdown 当作下游机器数据
- 返回多套展示结构让报告系统二次理解
- 在主报告字段中写 traceback 或恢复建议

### 3.2 实时报告 writer

实时报告由调度层调用：

```text
core/task_report_writer.py
```

它负责：

- 初始化 `REPORT.md`
- 写入源文件与沙盒备份模块
- step 开始时写入 `RUNNING`
- step 完成时替换为最终标准执行记录
- workflow / chain 结束时写入最终状态

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

```md
### Step N | {skill} | {status} | {elapsed_sec}s

#### Input
| key | value |
|---|---|

#### Output
| key | value |
|---|---|
```

`output` 中的数组或分组对象按字段名展开为明细表。报告层不生成一句话结论，不从旧 Markdown 中反推统计。

## 6. 对比类报告

对比类 skill 的报告字段必须使用标准执行记录 `output` 中的四类：

- `matched_same`
- `matched_different`
- `only_source`
- `only_target`

`compare_result.json` 可以继续保留机器字段 `paired`、`only_a`、`only_b`、`actionability`，但这些字段不直接作为主报告标题。

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
