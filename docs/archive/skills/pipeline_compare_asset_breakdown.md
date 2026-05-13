# pipeline_compare_asset 拆解

更新时间: 2026-05-12

本文档按运行时总规范 v1 拆解 `pipeline_compare_asset` 的职责、输入、输出、已确认决策和本轮落地结果。

## 1. 定位

`pipeline_compare_asset` 是 DCC 无关的独立资产对比节点。

职责：

- 读取 source 与 target 的 `_info.json` 或 `.abc`
- 执行三步漏斗配对
- 输出标准 `compare_result.json`
- 返回标准执行记录 `output`，供报告系统直接渲染

不是：

- DCC 场景打开节点
- mesh 构建节点
- rig 同步执行节点
- 拼装决策执行节点
- 材质赋予节点
- 独立 Markdown 落盘节点

## 2. 所属链路

只读对比链路：

```text
source_info.json
target_info.json
  -> pipeline_compare_asset
  -> compare_result.json
```

拼装链路使用同一套核心对比逻辑，但主 workflow 不再要求本 skill 先落 `compare_result.json`：

```text
source.abc + Maya 当前 rig 场景
  -> maya_compare_asset_in_scene
  -> 场景内采集 target rig info
  -> 写 pre compare_result.json
pre compare_result.json + source.abc
  -> maya_sync_rig_incremental
  -> 根据 compare_result 执行拼装
```

后置验证链路：

```text
source.abc + Maya 当前 rig 场景
  -> maya_compare_asset_in_scene
  -> post_compare_result.json
```

## 3. 输入契约

### 3.1 框架输入

| 字段 | 含义 |
|---|---|
| `payload.run_dir` | 任务沙盒目录 |
| `payload.info_dir` / `payload.extra_params.info_dir` | 任务沙盒 `.info` 目录 |
| `payload.project` | 项目代号 |
| `payload.asset_name` | 资产名 |

### 3.2 parameters

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `input_source` | string | 是 | source 侧 `_info.json` 或 `.abc` |
| `input_target` | string | 是 | target 侧 `_info.json` 或 `.abc` |
| `label_source` | string | 否 | source 标签；为空从路径推断 |
| `label_target` | string | 否 | target 标签；为空从路径推断 |
| `output_path` | string | workflow 必填 | `compare_result.json` 输出路径，必须位于 `{sandbox}/.info/` |

兼容旧参数：

- `input_a` / `input_b`
- `label_a` / `label_b`

新 workflow 和文档不再推荐旧参数。

## 4. 输出契约

文件产物：

```text
{sandbox}/.info/{source_stem}_vs_{target_stem}_compare_result.json
{sandbox}/.info/{rig_stem}_pre_compare_result.json
{sandbox}/.info/{rig_stem}_post_compare_result.json
```

标准执行记录 `output`：

```json
{
  "output_path": "..._compare_result.json",
  "matched_total": 25,
  "matched_same": [],
  "matched_different": [],
  "only_source": [],
  "only_target": []
}
```

`output_path` 是独立对比结果文件。报告展示字段直接进入标准执行记录 `output`。

## 5. compare_result JSON

顶层字段：

- `schema_version`: 固定 `compare_result.v1`
- `generated_at`: ISO 时间
- `inputs`: source/target 输入路径与标签
- `compare`: `core.asset_info_schema.compare()` 完整返回

`compare` 必含：

- `paired`
- `only_a`
- `only_b`
- `merge_groups`
- `split_groups`
- `pairing_groups`
- `target_only_dags`

## 6. 标签语义

算法层标签：

- `IDENTICAL`
- `ORIG_INJECT`
- `MODIFIED`
- `MERGE`
- `SPLIT`
- `NEW`
- `DELETE`

用户视角 4 去向：

- `matched_same` = `IDENTICAL + ORIG_INJECT`
- `matched_different` = `MODIFIED + MERGE + SPLIT`
- `only_source` = source 独有
- `only_target` = target 独有

拼装逻辑应直接消费 compare_result 中的 `pairing_groups`：

- `IDENTICAL`
- `ORIG_INJECT`
- `PAIRED`
- `UNPAIRED`

target 独有项单独走 `target_only_dags`。

## 7. 已确认决策

- workflow 必须显式传 `output_path` 到 `.info`。
- 单技能兜底只从 `info_dir` / `run_dir` / `task_id` 推导 `.info`。
- 无法推导沙盒 `.info` 时返回 `ERROR`。
- 不允许回退到 source 或 target 输入文件同目录。
- `compare_result.json` 只保留输入记录和 `compare` 结果，不嵌入 `source_info`。
- 标准执行记录 `output` 包含 `output_path` 和四类对比结果。
- 空几何作为合法事实参与对比，不在本 skill 额外诊断或修复。
- Markdown 报告只渲染标准执行记录，不消费旧正文块。
- 主拼装 workflow 使用 `maya_compare_asset_in_scene` 在 Maya 场景内生成 pre/post compare_result。
- `maya_sync_rig_incremental` 必须消费前置 compare_result，不再内部重算对比。

## 8. 本轮代码落地

已更新：

- `skills/pipeline_compare_asset/pipeline_compare_asset.py`
- `skills/pipeline_compare_asset/SKILL.md`
- `tests/test_compare_result_contract.py`
- `core/compare_result_io.py`
- `tools/verify_mcp_contract.py`

行为变化：

- 未传 `output_path` 但能解析 `info_dir` 时，默认写入 `.info`。
- 未传 `output_path` 且无法解析沙盒时，返回 `ERROR`。
- 删除输入同目录默认输出策略。
- `compare_result.json` 不再写入 `source_info`。
- 旧展示统计迁移到标准执行记录 `output`。
- 静态门禁阻断旧 fallback 逻辑回归。

## 9. 验证点

静态验证：

- workflow 中 `pipeline_compare_asset.output_path` 包含 `{{input.info_dir}}`
- `output_path` 文件名包含 `_compare_result.json`
- 代码中不存在输入目录 fallback

运行验证：

- 显式 `output_path` 时输出到指定 `.info`
- 仅传 `info_dir` 时自动输出到 `.info`
- 无 `output_path/info_dir/run_dir/task_id` 时不写输入目录并返回 `ERROR`
- `compare_result.json` 包含 `compare.pairing_groups` 与 `compare.target_only_dags`
- `compare_result.json` 不包含 `source_info`

## 10. 拆解结论

`pipeline_compare_asset` 已收紧为纯数据对比与 compare_result 生产节点。

Maya 场景内对比由 `maya_compare_asset_in_scene` 负责；Maya target 采集已抽到 `dccs.maya.asset_info_collector`，供 `maya_build_asset_info`、`maya_compare_asset_in_scene` 和 `maya_sync_rig_incremental` 复用。
