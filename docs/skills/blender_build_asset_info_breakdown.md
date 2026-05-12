# blender_build_asset_info 拆解

更新时间: 2026-05-12

本文档按运行时总规范 v1 拆解 `blender_build_asset_info` 的职责、输入、输出、已确认决策和本轮落地结果。

## 1. 定位

`blender_build_asset_info` 是 Blender 侧几何资产信息采集节点。

职责：

- 从 Blender 沙盒场景读取 source 侧几何事实
- 生成标准 `_info.json`
- 供 `pipeline_compare_asset` 或其他 pipeline 节点消费

不是：

- ABC 导出节点
- 贴图采集节点
- 面级材质分配节点
- 报告生成节点
- 场景修改节点

贴图、透明度、UDIM 和面级材质归 `blender_extract_materials`。

## 2. 所属链路

只读对比链路：

```text
Blender tex scene
  -> blender_build_asset_info
  -> source_info.json
  -> pipeline_compare_asset
```

对比后拼装链路中，source 主数据优先用 ABC；本节点用于轻量预检、只读对比或无 ABC 的降级输入。

## 3. 输入契约

### 3.1 框架输入

| 字段 | 含义 |
|---|---|
| `payload.source_path` | Blender 源场景沙盒副本路径 |
| `payload.project` | 项目代号 |
| `payload.asset_name` | 资产名 |
| `payload.run_dir` | 任务沙盒目录 |
| `payload.info_dir` / `payload.extra_params.info_dir` | 任务沙盒 `.info` 目录 |

### 3.2 parameters

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `info_path` | string | workflow 必填 | `_info.json` 输出路径，必须位于 `{sandbox}/.info/` |
| `cache_group` | string | 是 | Blender 根对象名，由项目配置或 workflow 传入 |

### 3.3 推荐 workflow 写法

```json
{
  "step_id": "build_source_info",
  "skill_id": "blender_build_asset_info",
  "parameters": {
    "info_path": "{{input.info_dir}}/{{input.source_path | stem}}_info.json",
    "cache_group": "{{config.stages.tex.geom_roots.0}}"
  }
}
```

Blender 侧查找时会取 `cache_group` 最后一段，例如 `|Group|cache` 会转为 `cache`。

## 4. 输出契约

文件产物：

```text
{sandbox}/.info/{source_stem}_info.json
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

- DAG / 层级路径
- 顶点数
- 顶点坐标数组

`textures` 保留为空 `{}`，只为兼容 `asset_info_schema`。本 skill 不写贴图数据。

坐标转换：

```text
x * 100
z * 100
-y * 100
```

坐标精度保留 4 位小数。

## 6. 已确认决策

- `info_path` 为空时，单技能可自动写任务沙盒 `.info`。
- 默认文件名为 `{source_stem}_info.json`。
- 每次任务都重新采集并覆盖本次沙盒内的 `_info.json`，不做 MTime 缓存复用。
- `cache_group` 必须从项目配置 / workflow 传入，不在 skill 内猜默认组。
- 顶点采集使用原始 `obj.data`，不走 evaluated mesh，不应用修改器结果。
- 去掉贴图职责，避免与 `blender_extract_materials` 冲突。
- workflow 中同步显式传 `info_path` 和 `cache_group`。

## 7. 本轮代码落地

已更新：

- `skills/blender_build_asset_info/blender_build_asset_info.py`
- `skills/blender_build_asset_info/SKILL.md`
- `workflows/tex_to_rig_verify.json`
- `workflows/blender_tex_export.json`
- `workflows/blender_to_maya_full_build.json`
- `core/tasks.py`
- `tools/verify_mcp_contract.py`

行为变化：

- 未传 `info_path` 时不再回退源文件同目录。
- 已移除 MTime 缓存命中分支，每次执行都重新采集。
- `cache_group` 缺失直接返回 `ERROR`。
- 不再返回 `tex_count`。
- 不再采集材质/贴图。
- workflow 的 ABC / JSON / materials / compare_result 均显式指向 `.info`。
- 调度层向 skill payload 透传 `run_dir`、`info_dir` 和 `extra_params`。

## 8. 验证点

静态验证：

- workflow 中 `blender_build_asset_info.info_path` 包含 `{{input.info_dir}}`
- `info_path` 文件名包含 `_info.json`
- receipt 路径只使用 `output_path`
- workflow 中 `cache_group` 来自 `{{config...}}`

运行验证：

- 用小型 `.blend` 采集成功
- 输出位于 `.info`
- `pipeline_compare_asset` 能消费该 JSON
- 源 `.blend` 未被修改

## 9. 拆解结论

`blender_build_asset_info` 已收紧为只读几何采集节点。

后续继续按同样方式审计下一个 skill：先核对 `SKILL.md` 和代码实际行为，再拆决策项，再落代码。
