# 对比与拼装 Pipeline 梳理文档

更新时间: 2026-05-12

运行时总规则见 [pipeline_runtime_contract_v1.md](pipeline_runtime_contract_v1.md)。本文档只记录资产对比 Compare 和绑定拼装 Assembly/Sync 的当前权威链路。

## 1. 目标结论

所有源文件只读，任务先进入沙盒。机器中间产物只写当前任务沙盒的 `.info`：

```text
projects/{project}/{YYYYMMDD_HHMMSS}_{asset}/.info/
```

沙盒根目录只保留输入源文件副本、最终升版本场景、统一 Markdown 报告和 `manifest.json`。最终 Maya 文件按现有版本递增保存，不追加 `_synced`。

## 2. 当前主链路

只看差异：

```text
Blender source
  -> blender_export_abc
Maya target rig
  -> maya_compare_asset_in_scene
       - 场景内采集 target ShapeOrig 信息
       - 读取 source ABC
       - 调用 core.asset_info_schema.compare()
       - 写 pre compare_result.json
```

对比后拼装：

```text
Blender source
  -> blender_export_abc
  -> blender_extract_materials
Maya target rig
  -> maya_compare_asset_in_scene 生成 pre compare_result
  -> maya_sync_rig_incremental 消费 compare_result + source_abc 执行拼装
  -> maya_fix_shape_names
  -> maya_apply_materials
  -> maya_compare_asset_in_scene 生成 post compare_result
  -> save_scene 升版本保存
```

`pipeline_compare_asset` 保留为纯数据对比入口。当 source 与 target 都已经是 `_info.json` 或 ABC 时使用；主拼装 workflow 优先用 `maya_compare_asset_in_scene`，避免“落 JSON → 再开 Maya”的流程损耗。

## 3. 数据契约

### 3.1 asset_info

生产者：

- `blender_build_asset_info`
- `maya_build_asset_info`
- `core.abc_reader.read_abc_as_info`
- `dccs.maya.asset_info_collector.collect_scene_info`

Maya rig 采集规则：

- 从 `cache_group` 全层级子孙里自身带 mesh shape 的 transform 出发。
- mesh key 使用标准非 intermediate mesh shape 的绝对 DAG 路径；transform 可见性不参与过滤。
- 顶点数据只来自同 transform 下命名规范且唯一有效的 `{transform}ShapeOrig`。
- 找不到标准 Orig 时保留 mesh 条目但写空几何；不在采集器里诊断或修复。

### 3.2 ABC

ABC 是拼装 source 主数据，包含完整拓扑和 UV。`blender_export_abc`、`maya_export_abc`、`pipeline_export_abc_auto` 的输出必须在 `.info`。

### 3.3 材质 JSON

`blender_extract_materials` 生产 `_materials.json`，`maya_apply_materials` 消费。ABC 导出不隐式生成材质 JSON。

### 3.4 compare_result

生产者：

- `maya_compare_asset_in_scene`
- `pipeline_compare_asset`

消费者：

- `maya_sync_rig_incremental`
- 自动巡航、Dashboard、统一任务报告

结构：

```json
{
  "schema_version": "compare_result.v1",
  "generated_at": "...",
  "inputs": {
    "input_source": "...",
    "input_target": "...",
    "label_source": "tex",
    "label_target": "rig"
  },
  "compare": {}
}
```

`compare_result` 不嵌入 `source_info`。sync 如需完整 source 拓扑，必须同时传 `source_abc` 或 `source_info`，推荐 `source_abc`。

## 4. 技能职责边界

| 技能 | 职责 |
|---|---|
| `blender_export_abc` | 只导出 ABC + FaceSet |
| `blender_extract_materials` | 只采集 per-face 材质信息 |
| `blender_build_asset_info` | 只采 Blender 几何 `_info.json` |
| `maya_build_asset_info` | 只写出 Maya rig 几何 `_info.json`，采集逻辑与场景内对比共用 |
| `maya_compare_asset_in_scene` | 当前 Maya 场景内采集 target rig 信息，与 source ABC/_info 对比，写 compare_result |
| `pipeline_compare_asset` | 纯 JSON/ABC 对比，写 compare_result |
| `maya_sync_rig_incremental` | 只消费前置 compare_result 和 source 数据执行拼装，不独立重算对比 |
| `maya_fix_shape_names` | 修复 Shape/Orig 命名与死 Orig |
| `maya_apply_materials` | 消费 `_materials.json` 按面赋材质 |
| `save_scene` | 破坏性链路最后一步，沙盒内升版本保存 |

## 5. 当前 Workflow

### `tex_to_rig_verify`

```text
blender_export_abc
maya_compare_asset_in_scene
```

输出：

```text
.info/{source_stem}.abc
.info/{source_stem}_vs_{rig_stem}_compare_result.json
```

### `tex_to_rig_verify_and_sync`

```text
blender_export_abc
blender_extract_materials
maya_compare_asset_in_scene (pre)
maya_sync_rig_incremental
maya_fix_shape_names
maya_apply_materials
maya_compare_asset_in_scene (post)
save_scene
```

输出：

```text
.info/{source_stem}.abc
.info/{source_stem}_materials.json
.info/{rig_stem}_pre_compare_result.json
.info/{rig_stem}_post_compare_result.json
{rig_stem_vNext}.ma
```

## 6. 验证状态

已验证：

- `python tools/verify_mcp_contract.py`
- `python tests/test_compare_result_contract.py`
- `python tests/test_sync_action_dispatch.py`
- `python tests/test_pipeline_compare.py`
- `python tests/test_rig_sync_profile.py`
- `python tests/test_compare_contract.py`
- `python tests/test_write_task_report.py`
- `python tools/run_auto_cruise_test.py`

`mihouwang` 真实巡航结果：post compare 阻断差异 0，沙盒内升版本保存成功。

## 7. 后续风险点

- `maya_sync_rig_incremental` 已完成第一轮专项治理：输入/compare_result 契约层、失败阶段、回执语义和普通 Python 单测已补齐；后续可继续细拆权重/BS 投射大函数。
- 文档归档文件和历史方案需要单独治理，避免旧方案被误当权威。
- 还需要更多资产巡航覆盖，例如结构不同、隐藏 mesh 较多、缺 Orig 的绑定文件。
