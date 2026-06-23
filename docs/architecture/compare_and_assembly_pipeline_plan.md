# 对比与拼装 Pipeline 当前规范

更新时间: 2026-05-14

运行时边界见 `pipeline_runtime_contract_v1.md`。本文只记录资产对比 Compare、层级修复、绑定拼装 Assembly/Sync、材质赋予和验证的当前权威链路。

## 1. 当前结论

所有源文件只读，任务先进入沙盒。机器中间产物只写当前任务沙盒的 `.info`；用户主要查看沙盒根目录里的 `REPORT.md` 和最终升版本 `.ma`。

```text
projects/{project}/{YYYYMMDD_HHMMSS}_{asset}/
  REPORT.md
  manifest.json
  source scene copy
  rig scene copy
  final version-up scene
  .info/
    source.abc
    source_materials.json
    pre_compare_result.json
    post_compare_result.json
```

最终 Maya 文件按现有版本递增保存，不追加 `_synced`。

## 2. 主 workflow

当前主线是 `tex_to_rig_verify_and_sync`：

```text
resolve_asset_files
-> blender_export_abc
-> blender_extract_materials
-> maya_check_asset_hierarchy (pre)
-> maya_fix_asset_hierarchy
-> maya_compare_asset_in_scene (pre)
-> maya_sync_rig_incremental
-> maya_check_asset_hierarchy (post)
-> maya_fix_shape_names
-> maya_apply_materials
-> maya_compare_asset_in_scene (post)
-> save_scene
```

关键规则：

- `resolve_asset_files` 是唯一入口门：只传资产名时查服务器最新 tex/rig，显式传 `source_path` / `rig_path` 时校验后透传。
- 缺 tex/rig 时，`resolve_asset_files` 返回 `ERROR`、`missing_inputs` 和搜索路径，workflow 立即中断，后续 DCC skill 不运行。
- 拼装前 check 一次，fix 一次；拼装后再 check 一次。拼装后 check 仍失败时，要么是 fix 没修干净，要么是 sync 重新破坏了层级。
- `maya_fix_asset_hierarchy` 消费前置 check 的 `output.result`，不重新发现另一套目标。
- `maya_sync_rig_incremental` 消费前置 `compare_result` 和 `source_abc`，同步阶段不能独立重算另一份对比。
- `save_scene` 必须是破坏性链路最后一步。

## 3. 层级规则

标准几何层级：

```text
|Group|Geometry|RIG_geo
|Group|Geometry|cache
```

规则：

- 旧绑定里的 `|*|geo` 视为 legacy rig geometry root，必须迁移到 `|Group|Geometry|RIG_geo`。
- 新 ABC cache 拼装输出到 `|Group|Geometry|cache`。
- 例如第一层级是 `mayouB|geo` 时，修复结果应是 `|Group|Geometry|RIG_geo`，不能直接删除 `mayouB|geo` 导致绑定系统丢失。
- 顶层散落节点由 `maya_check_asset_hierarchy` 输出 `extra_top_nodes` / `manual_review_top_nodes`；是否阻断由 workflow 参数控制。
- check/fix 报告只展示核心问题列表和数量，不铺完整机器 JSON。

## 4. 数据契约

### 4.1 asset_info

生产者：

- `blender_build_asset_info`
- `maya_build_asset_info`
- `core.abc_reader.read_abc_as_info`
- `dccs.maya.asset_info_collector.collect_scene_info`

Maya rig 采集规则：

- 从配置的 rig 几何根全层级子孙中采集自身带 mesh shape 的 transform。
- mesh key 使用标准非 intermediate mesh shape 的绝对 DAG 路径。
- transform 可见性不参与过滤。
- 顶点数据优先来自同 transform 下命名规范且唯一有效的 `{transform}ShapeOrig`。
- 找不到标准 Orig 时保留 mesh 条目但写空几何；诊断由 compare/report 暴露，不由采集器修复。

### 4.2 ABC

ABC 是拼装 source 主数据，包含完整拓扑和 UV。`blender_export_abc`、`maya_export_abc`、`pipeline_export_abc_auto` 的输出必须在 `.info`。
Alembic archive 自带的顶层 `ABC` 只属于文件容器，不属于业务 DAG；`core.abc_reader.read_abc_as_info` 输出 mesh key 时必须剥离该前缀，后续 Maya 拼装不得生成 `|ABC` 顶层。

### 4.3 材质 JSON

`blender_extract_materials` 生产 `_materials.json`，`maya_apply_materials` 消费。ABC 导出不隐式生成材质 JSON。
材质 JSON 只把真实 color/albedo/diffuse 贴图写入 `color.type="texture"`；AO、normal、roughness、metallic、height、mask 等非 color 贴图不作为 color 贴图输出。没有真实 color 贴图时按材质球纯色赋予。

### 4.4 compare_result

生产者：

- `maya_compare_asset_in_scene`
- `pipeline_compare_asset`

消费者：

- `maya_sync_rig_incremental`
- 自动巡航
- Dashboard
- 统一任务报告

`compare_result` 是机器契约，不是报告正文。它可以保留完整 DAG、配对关系、算法标签和审计字段；报告只渲染 receipt `output` 中的统计和短明细。
报告 Details 明细不得再走旧 `report_sections`；compare/sync/hierarchy/resolve 的短明细统一放在 `output.*_items` 或语义化 `output` 列表字段。

## 5. 技能职责边界

| 技能 | 职责 |
|---|---|
| `resolve_asset_files` | 解析或校验 tex/rig 输入路径，缺失时阻断 workflow |
| `blender_export_abc` | 只导出 ABC + FaceSet |
| `blender_extract_materials` | 只采集 per-face 材质信息 |
| `maya_check_asset_hierarchy` | 只检查 rig/cache 标准层级和顶层异常 |
| `maya_fix_asset_hierarchy` | 只按 check 结果修复层级，迁移 legacy `|*|geo` |
| `maya_compare_asset_in_scene` | 当前 Maya 场景内采集 target rig 信息，与 source ABC/_info 对比，写 compare_result |
| `pipeline_compare_asset` | 纯 JSON/ABC 对比，写 compare_result |
| `maya_sync_rig_incremental` | 只消费前置 compare_result 和 source 数据执行拼装 |
| `maya_fix_shape_names` | 修复 Shape/Orig 命名与死 Orig |
| `maya_apply_materials` | 消费 `_materials.json` 按面赋材质 |
| `save_scene` | 沙盒内升版本保存 |

## 6. 对比与配对输出

对比类 skill 的标准 `output` 使用四类用户视角事实：

| 字段 | 含义 |
|---|---|
| `matched_total` | `matched_same + matched_different` |
| `matched_same` | source 在 target 中找到可接受配对，包含 `IDENTICAL` 和 `ORIG_INJECT` |
| `matched_different` | source 找到配对但几何不同，需要后续处理或审查 |
| `only_source` | 只存在于 source |
| `only_target` | 只存在于 target |
| `*_items` | 对应短明细，只放 source / target / action / layer 等核心字段 |

算法层仍可保留 `IDENTICAL`、`ORIG_INJECT`、`MODIFIED`、`MERGE`、`SPLIT`、`NEW`、`DELETE` 等标签；它们服务于 sync 和审计，不直接作为报告主字段。配对算法细节见 `mesh_pairing_phased_logic.md`。

## 7. displayLayer 命名

同步阶段按 source 资产 mesh 命名 displayLayer：

- 单源 PAIRED：`{asset_mesh_name}_Layer`。
- 多个 source 匹配一个 rig：`A_B_Layer`。
- 名称过长或冲突时退到 `A_GRP_Layer`。
- layer 名称不能直接复用 rig mesh transform 名，避免把“配对关系”和“同类命名”混在一起。

## 8. 当前验证状态

普通门禁：

- `python tools/verify_mcp_contract.py`
- `python tests/test_compare_result_contract.py`
- `python tests/test_sync_action_dispatch.py`
- `python tests/test_pipeline_compare.py`
- `python tests/test_rig_sync_profile.py`
- `python tests/test_workflow_input_contract.py`
- `python tests/test_docs_knowledge_contract.py`

真实资产验证：

- `mihouwang`：2026-05-11/12 主链路巡航 PASS，post 阻断差异 0，沙盒升版本保存。
- `ciweiguai`：2026-05-12 Blender→Maya 构建和主链路巡航 PASS，覆盖材质与 73 mesh 构建。
- `maYouB`：2026-05-12/13 主链路巡航 PASS，验证旧 `|*|geo` 归一到 `|Group|Geometry|RIG_geo`。
- `cdfbaixingG`：主 workflow 跑通，用于报告格式、层级修复和配对命名验证。
- `cdfbaixingJ`：2026-05-14 `WORKFLOW_SUCCESS`，post compare 22/22，0 阻断差异。
- `cdfbaixingL`：2026-05-14 `WORKFLOW_SUCCESS`，post compare 22/22，0 阻断差异。
- `cdfbaixingH`：当前缺 `rig/rigMaster`，预期 Step 1 中断并报告缺失路径。
- `xycrowdbig`：当前缺 `tex/texMaster` 与 `rig/rigMaster`，预期 Step 1 中断。

## 9. 当前风险点

- Live BS / 动态 BlendShape 产品化仍未完全并入主 workflow；不能把验证场景当作正式同步能力。
- 真实资产覆盖还要继续增加结构不同、隐藏 mesh 多、缺 Orig、多语义 merge 的绑定文件。
