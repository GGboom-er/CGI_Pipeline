# ABC 导出类 skill 拆解

更新时间: 2026-05-12

本文档按运行时总规范 v1 拆解 ABC 导出类节点的职责、输入、输出、已确认决策和本轮落地结果。

涉及 skill：

- `blender_export_abc`
- `maya_export_abc`
- `pipeline_export_abc_auto`

## 1. 定位

ABC 导出类节点负责把 DCC 场景中的几何数据导出为 Alembic `.abc`。

职责：

- 读取当前已打开的沙盒场景
- 导出 `.abc` 到任务沙盒 `.info`
- 返回 `outputs.output_path` 供下游读取

不是：

- `_info.json` 生产节点
- `_materials.json` 生产节点
- 场景保存节点
- 发布节点
- rig 同步执行节点

## 2. 所属链路

Blender source 到 Maya rig 拼装：

```text
blender_export_abc
  -> source.abc
  -> pipeline_compare_asset
  -> maya_sync_rig_incremental
```

Maya source 进入同一套对比链路：

```text
maya_export_abc
  -> source.abc
  -> pipeline_compare_asset
```

自动入口：

```text
pipeline_export_abc_auto
  -> 根据扩展名路由到 blender_export_abc 或 maya_export_abc
```

## 3. 输入契约

### 3.1 通用框架输入

| 字段 | 含义 |
|---|---|
| `payload.source_path` | DCC 源场景沙盒副本路径 |
| `payload.run_dir` | 任务沙盒目录 |
| `payload.info_dir` / `payload.extra_params.info_dir` | 任务沙盒 `.info` 目录 |

### 3.2 blender_export_abc parameters

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `abc_path` | string | workflow 必填 | `.abc` 输出路径，必须位于 `{sandbox}/.info/` |
| `cache_group` | string | 是 | Blender 几何根对象，由项目配置或 workflow 传入 |

### 3.3 maya_export_abc parameters

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `abc_path` | string | workflow 必填 | `.abc` 输出路径，必须位于 `{sandbox}/.info/` |
| `frame_range` | array | 否 | 导出帧范围 `[start, end]` |
| `root_nodes` | string | 否 | 半角逗号分隔的导出根节点；为空导出非默认相机顶层 |

### 3.4 pipeline_export_abc_auto parameters

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `abc_path` | string | workflow 必填 | 透传给子级导出 skill 的 `.abc` 输出路径 |

## 4. 输出契约

文件产物：

```text
{sandbox}/.info/{source_stem}.abc
```

receipt.outputs：

```json
{
  "output_path": ".../{source_stem}.abc"
}
```

`output_path` 是下游唯一引用路径。

## 5. 已确认决策

- workflow 必须显式传 `abc_path` 到 `.info`。
- 单技能兜底只从 `info_dir` / `run_dir` / `task_id` 推导 `.info`。
- 无法推导沙盒 `.info` 时返回 `ERROR`。
- 不允许回退到源文件同目录。
- `pipeline_export_abc_auto` 不允许按发布路径推导 ABC。
- ABC 导出不生产 `_info.json` 或 `_materials.json`。

## 6. 本轮代码落地

已更新：

- `skills/blender_export_abc/blender_export_abc.py`
- `skills/blender_export_abc/SKILL.md`
- `skills/maya_export_abc/maya_export_abc.py`
- `skills/maya_export_abc/SKILL.md`
- `skills/pipeline_export_abc_auto/pipeline_export_abc_auto.py`
- `skills/pipeline_export_abc_auto/SKILL.md`
- `tools/verify_mcp_contract.py`

行为变化：

- `maya_export_abc` 正式声明并读取 `abc_path`。
- `blender_export_abc` 不再从 `source_path` 同目录推导默认 `.abc`。
- `pipeline_export_abc_auto` 不再通过 `AssetResolver.build_publish_path()` 推导输出。
- 三个导出入口都统一使用任务沙盒 `.info` 兜底。

## 7. 验证点

静态验证：

- workflow 中 `abc_path` 包含 `{{input.info_dir}}`
- 导出类 skill 不包含源目录/发布目录 fallback 逻辑
- receipt 路径只使用 `output_path`

运行验证：

- 显式 `abc_path` 时输出到指定 `.info`
- 仅传 `info_dir` 时自动输出到 `.info`
- 无 `abc_path/info_dir/run_dir/task_id` 时返回 `ERROR`
- 源 `.ma/.mb/.blend` 未被修改

## 8. 拆解结论

ABC 导出类 skill 已统一为机器中间产物生产节点，输出只进任务沙盒 `.info`。

后续默认输出策略专项已覆盖：`blender_build_asset_info`、`maya_build_asset_info`、`pipeline_compare_asset`、`blender_export_abc`、`maya_export_abc`、`pipeline_export_abc_auto`。
