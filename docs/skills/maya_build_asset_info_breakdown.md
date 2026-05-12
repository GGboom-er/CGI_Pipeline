# maya_build_asset_info 拆解

更新时间: 2026-05-12

本文档按运行时总规范 v1 拆解 `maya_build_asset_info` 的职责、输入、输出、已确认决策和本轮落地结果。

## 1. 定位

`maya_build_asset_info` 是 Maya 侧几何资产信息采集节点。

职责：

- 从 Maya 沙盒场景读取 target rig 或 Maya source 的几何事实
- 生成标准 `_info.json`
- 从每个标准 mesh 同 transform 下命名规范且唯一的 ShapeOrig 采集几何
- 供 `pipeline_compare_asset` 等纯数据对比入口消费

不是：

- 贴图扫描节点
- 面级材质采集节点
- UV set 清理节点
- ABC 导出节点
- 场景保存节点
- ShapeOrig 清理节点
- ShapeOrig 诊断报告节点
- 主拼装 workflow 的前置对比节点

贴图检查归 `maya_check_textures`。Blender 来源的面级材质归 `blender_extract_materials` 和 `maya_apply_materials`。
ShapeOrig 清理由 `maya_fix_shape_names` 或后续专用 workflow 负责。
主对比/拼装 workflow 使用 `maya_compare_asset_in_scene` 在当前 Maya 场景内采集 target 并写 `compare_result`，避免先落 target `_info.json` 再对比。

## 2. 所属链路

纯数据对比链路：

```text
Maya rig scene
  -> maya_build_asset_info
  -> target_info.json
  -> pipeline_compare_asset
```

主对比/拼装链路中，本技能的采集代码被 `maya_compare_asset_in_scene` 复用，但不单独作为 workflow 步骤：

```text
Maya rig scene + source ABC
  -> maya_compare_asset_in_scene
  -> pre compare_result.json
  -> maya_sync_rig_incremental
  -> maya_compare_asset_in_scene
  -> post compare_result.json
```

## 3. 输入契约

### 3.1 框架输入

| 字段 | 含义 |
|---|---|
| `payload.source_path` | Maya 源场景沙盒副本路径 |
| `payload.project` | 项目代号 |
| `payload.asset_name` | 资产名 |
| `payload.run_dir` | 任务沙盒目录 |
| `payload.info_dir` / `payload.extra_params.info_dir` | 任务沙盒 `.info` 目录 |

### 3.2 parameters

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `info_path` | string | workflow 必填 | `_info.json` 输出路径，必须位于 `{sandbox}/.info/` |
| `cache_group` | string | 是 | Maya 几何根组，由项目配置或 workflow 传入 |

### 3.3 推荐 workflow 写法

```json
{
  "step_id": "build_target_info",
  "skill_id": "maya_build_asset_info",
  "parameters": {
    "info_path": "{{input.info_dir}}/{{input.rig_path | stem}}_info.json",
    "cache_group": "{{config.stages.rig.geom_roots.0}}"
  }
}
```

`cache_group` 可传 `|Group|Geometry|cache`。代码会按完整路径、末段名称和常见 Maya rig 层级候选查找。

## 4. 输出契约

文件产物：

```text
{sandbox}/.info/{scene_stem}_info.json
```

receipt.outputs：

```json
{
  "output_path": ".../_info.json"
}
```

`output_path` 是下游唯一引用路径。mesh 数量写入 `summary_count`，不作为 workflow 段间数据传递。

## 5. `_info.json` 数据内容

顶层字段：

- `source_file`
- `meshes`
- `textures`

每个 mesh 包含：

- 当前 shape 的 Maya 绝对 DAG 路径
- 顶点数
- 顶点坐标数组

`textures` 保留为空 `{}`，只为兼容 `asset_info_schema`。本 skill 不写贴图数据。

坐标使用 Maya 世界空间厘米单位，精度保留 4 位小数。

未采到标准 Orig 的 mesh 仍保留条目：

```json
{
  "vertices": 0,
  "vert_positions": []
}
```

这代表“cache 组内存在该 mesh，但本技能没有采到符合标准结构的 Orig 几何”。后续对比/报告节点负责暴露问题。

## 6. ShapeOrig 采集规则

- 先列出 `cache_group` 全层级子孙里自身带 mesh shape 子物体的 transform。
- 再在每个 transform 下检查非 `intermediateObject` 的标准 shape。
- 可见性不作为过滤条件，隐藏绑定 mesh 也参与采集。
- 标准结构是 transform、`{transform}Shape`、`{transform}ShapeOrig` 都在同一个 transform 层级下。
- 每个 transform 下必须有且只有一个非 intermediate 标准 shape。
- `meshes` 的 key 使用当前 shape 的 Maya 绝对 DAG 路径；即使几何来自 ShapeOrig，也不使用 Orig 名称作为 key。
- 首选 `cmds.deformableShape(shape, originalGeometry=True)` 获取对应 Orig。
- official 返回值必须解析为唯一存在、位于同 transform 下且命名为 `{transform}ShapeOrig` 的 intermediate mesh。
- official 未命中时，fallback 到同 transform 下的 intermediate mesh。
- fallback 只接受唯一 `outMesh` 有下游连接且命名为 `{transform}ShapeOrig` 的 intermediate mesh。
- 不满足标准结构时不猜、不选、不诊断，输出空几何。
- 不删除死 Orig，不自动调用 `maya_fix_shape_names`。

## 7. 已确认决策

- `info_path` 为空时，单技能可自动写任务沙盒 `.info`。
- 默认文件名为 `{source_stem}_info.json`。
- `cache_group` 必须从项目配置 / workflow 传入，不在 skill 内默认猜 `cache`。
- 所有 mesh 都只从同 transform 下命名规范且唯一的 ShapeOrig 采顶点。
- JSON mesh key 使用当前 shape 的 Maya 绝对 DAG 路径。
- 未采到标准 Orig 的 mesh 输出空几何，不降级到可见 Shape。
- 本 skill 不输出 ShapeOrig 状态、详情或修复建议。
- 去掉材质和贴图职责，避免与材质类 skill 冲突。
- workflow 中显式传 `info_path` 和 `cache_group`；主拼装 workflow 不需要单独调用本 skill。

## 8. 本轮代码落地

已更新：

- `skills/maya_build_asset_info/maya_build_asset_info.py`
- `skills/maya_build_asset_info/SKILL.md`
- `dccs/maya/asset_info_collector.py`

行为变化：

- 未传 `info_path` 时不再回退源文件同目录。
- `cache_group` 缺失直接返回 `ERROR`。
- 不再采集材质/贴图。
- 不再返回 `tex_count`。
- 不再返回 `AUDIT_FAILED` 作为 ShapeOrig 门禁。
- 不再接受 `allow_missing_orig` 参数。
- 缺失、命名不规范或无法唯一定位标准 Orig 的 mesh 输出 `vertices: 0`、`vert_positions: []`。
- receipt.outputs 只保留 `output_path`。

## 9. 验证点

静态验证：

- workflow 中 `maya_build_asset_info.info_path` 包含 `{{input.info_dir}}`
- `info_path` 文件名包含 `_info.json`、`_pre_sync.json` 或 `_post_sync.json`
- receipt 路径只使用 `output_path`
- workflow 中 `cache_group` 来自 `{{config...}}`

运行验证：

- 用小型 `.ma` 采集成功
- 输出位于 `.info`
- `pipeline_compare_asset` 能消费该 JSON
- 源 `.ma` 未被修改
- 无标准 Orig 的 mesh 在 JSON 中保留空几何

## 10. 拆解结论

`maya_build_asset_info` 已收紧为只读 Orig 几何采集节点。

主工作流的场景内对比由 `maya_compare_asset_in_scene` 承担；二者复用同一个 Maya 采集器。
