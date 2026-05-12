# 对比与拼装 Pipeline 梳理文档

更新时间: 2026-05-11

运行时总规则见 [pipeline_runtime_contract_v1.md](pipeline_runtime_contract_v1.md)。本文档只记录对比与拼装专项的链路拆分。

本文档用于统一 CGI Pipeline 中“资产对比 Compare”和“绑定拼装 Assembly / Sync”相关功能的边界、数据契约、文件落点和后续改造拆分。后续所有对比、ABC 拼装、材质迁移、Rig 增量同步相关改动，先以本文档作为讨论基准。

## 1. 目标结论

### 1.1 统一产物目录

所有机器中间产物必须写入当前任务沙盒的 `.info` 目录：

```text
Y:/GGbommer/scripts/CGI_Pipeline/projects/{project}/{YYYYMMDD_HHMMSS}_{asset}_{task_id}/.info/
```

机器中间产物包括：

- `_info.json`
- `_materials.json`
- `.abc`
- `compare_result.json`
- 其他供下游技能消费的结构化 JSON

沙盒根目录只保留：

- 被隔离后的输入源文件副本，如 `.blend` / `.ma`
- 按版本递增保存的最终输出场景
- 统一 Markdown 报告
- `manifest.json`

服务器路径 `X:/Project/.../pub` 只读，只作为源输入，不允许写入 JSON、ABC、报告或保存后的 Maya 场景。

### 1.2 两条主链路

只读对比链路：

```text
tex / source 场景
  -> blender_build_asset_info 或 ABC 读取
rig / target 场景
  -> maya_build_asset_info
两份 asset_info
  -> pipeline_compare_asset
  -> compare_result.json + 报告内容
```

对比后拼装链路：

```text
Blender tex
  -> blender_export_abc
  -> blender_extract_materials
Maya rig
  -> maya_build_asset_info
ABC + rig_info
  -> pipeline_compare_asset
  -> compare_result.json
compare_result + ABC
  -> maya_sync_rig_incremental
  -> maya_fix_shape_names
  -> maya_apply_materials
  -> maya_build_asset_info(post)
  -> pipeline_compare_asset(post)
  -> save_scene
```

## 2. 当前 MCP 架构事实

### 2.1 调用入口

MCP Server（`mcp_server/server.py`）启动时读取 `skills/*/SKILL.md`，动态注册大多数技能为 Tool。特殊入口由 `mcp_server/tools_operations.py` 手写注册：

- `pipeline_execute_workflow`: 跨 DCC 工作流编排入口
- `maya_execute_chain`: 单 DCC 多步骤链式执行入口
- `pipeline_compare_asset`: 直接对比入口
- `maya_sync_rig_incremental`: 增量同步入口
- `execute_skill`: 通用兜底入口

推荐对比/拼装主路径使用 `pipeline_execute_workflow`，让工作流引擎统一处理沙盒、分段、轮询、报告和 manifest。

### 2.2 Workflow 引擎行为

`core/tasks.py::execute_workflow` 当前会：

- 创建任务沙盒 `projects/{project}/{timestamp}_{asset}_{task_id}`
- 将 `source_path` 和 `extra_params` 中的 `.ma/.mb/.blend/.json` 隔离到沙盒
- 创建 `input.info_dir = {run_dir}/.info`
- 按技能 `dcc` 拆分 segment
- 将 segment 交给 `execute_skill_chain`
- 使用 `{{input.info_dir}}`、`{{outputs.step_id.output_path}}` 等模板传递路径
- 最终写统一报告和 `manifest.json`

因此 `.info` 目录已经由工作流层提供，后续重点是让所有对比/拼装 workflow 显式使用它。

## 3. 数据契约

### 3.1 asset_info JSON

生产者：

- `blender_build_asset_info`
- `maya_build_asset_info`
- `core.abc_reader.read_abc_as_info`

用途：

- 作为 `pipeline_compare_asset` 的输入
- 为 `maya_sync_rig_incremental` 提供 source 或 target 侧几何指纹

核心内容：

- mesh DAG 路径
- 顶点数
- 4 位小数坐标指纹
- Maya rig 文件中的 ShapeOrig 状态
- 顶层保留空 `textures: {}` 用于 schema 兼容；贴图和面级材质走独立 `_materials.json`

文件命名建议：

```text
{source_stem}_info.json
{rig_stem}_pre_sync.json
{rig_stem}_post_sync.json
```

### 3.2 ABC

生产者：

- `blender_export_abc`
- `maya_export_abc`
- `pipeline_export_abc_auto`

消费者：

- `pipeline_compare_asset`，可直接读取 ABC 做对比
- `maya_build_mesh_from_abc`，全量构建 mesh
- `maya_sync_rig_incremental`，重建 source 独有 mesh、补全拓扑和 UV

文件命名建议：

```text
{source_stem}.abc
```

### 3.3 材质 JSON

生产者：

- `blender_extract_materials`

消费者：

- `maya_apply_materials`
- `maya_sync_rig_incremental` 内部材质步骤的辅助路径

文件命名建议：

```text
{source_stem}_materials.json
```

落点规则：

- workflow 显式传 `output_path` 到 `{{input.info_dir}}`
- 单技能兜底也只写任务沙盒 `.info`
- 不回退源 `.blend` 同目录

### 3.4 compare_result JSON

生产者：

- `pipeline_compare_asset`

消费者：

- `maya_sync_rig_incremental`
- 后置验证报告
- Dashboard / 审计报告 / 人工复核

schema：

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
  "source_info": {},
  "compare": {}
}
```

文件命名建议：

```text
{rig_stem}_pre_compare_result.json
{rig_stem}_post_compare_result.json
{source_stem}_vs_{target_stem}_compare_result.json
```

## 4. 涉及技能清单

### 4.1 采集类

`blender_build_asset_info`

- DCC: Blender
- 只读
- 输出 source 侧几何 `_info.json`
- 不采集贴图或面级材质
- 已统一显式写入 `{{input.info_dir}}`

`maya_build_asset_info`

- DCC: Maya
- 只读
- 输出 target rig 侧 `_info.json`
- 绑定文件优先采 ShapeOrig
- 不采集贴图或面级材质
- 已统一显式写入 `{{input.info_dir}}`

### 4.2 转换类

`blender_export_abc`

- DCC: Blender
- 输出 `.abc`
- 拼装链路推荐用 ABC 作为 source 主数据
- 已在 `tex_to_rig_verify_and_sync` 中写入 `.info`
- 只负责 ABC 几何导出，不再隐式生成 `_materials.json`
- 已统一：workflow 显式传 `abc_path` 到 `.info`；单技能兜底也只写任务沙盒 `.info`

`maya_export_abc`

- DCC: Maya
- 输出 `.abc`
- 可用于 Maya source 资产进入同一套 compare/sync 逻辑
- 已统一：声明并读取 `abc_path`，默认输出只写任务沙盒 `.info`

`pipeline_export_abc_auto`

- DCC: pipeline
- 根据源文件扩展名路由到 Blender 或 Maya 导出
- 适合作为通用 ABC 入口
- 已统一：`abc_path` 透传给子级导出 skill，自动推导只写任务沙盒 `.info`

### 4.3 对比类

`pipeline_compare_asset`

- DCC: pipeline
- 纯数据对比，不改场景
- 输入支持 JSON 和 ABC
- 输出 `compare_result.json`
- 主算法在 `core/asset_info_schema.py::compare`
- 报告聚合在 `core/pairing_report.py`
- 已统一：workflow 显式传 `output_path` 到 `.info`；单技能兜底也只写任务沙盒 `.info`

### 4.4 拼装 / 同步类

`maya_sync_rig_incremental`

- DCC: Maya
- 修改 target rig 场景
- 推荐输入：`compare_result + source_abc`
- `compare_result` 优先，未传时内部即时 compare
- 内部会消费 `pairing_groups`，执行 IDENTICAL / ORIG_INJECT / PAIRED / UNPAIRED 动作
- 当前待专项治理：拆执行流、异常语义、失败回执、单测覆盖

`maya_build_mesh_from_abc`

- DCC: Maya
- 通过 PyAlembic 纯数据构建 mesh
- 全量构建链路使用
- `maya_sync_rig_incremental` 内部复用其 `create_mesh`

`maya_fix_shape_names`

- DCC: Maya
- 拼装后修复 Shape / ShapeOrig 命名
- 建议保留在 sync 之后、材质赋予之前或之后，具体顺序以后置采集稳定为准

`maya_apply_materials`

- DCC: Maya
- 消费 `_materials.json`
- 为 Maya mesh 创建材质并按面赋予

### 4.5 报告类

`write_task_report`

- DCC: pipeline
- 消费 audit JSONL，渲染统一报告
- 中间产物不应靠报告承载，报告只做人看

## 5. 现有 Workflow 清单与问题

### 5.1 `tex_to_rig_verify`

用途：只读对比。

当前步骤：

```text
blender_build_asset_info
maya_build_asset_info
pipeline_compare_asset
```

当前问题：

- 已修复：`blender_build_asset_info` 显式传 `info_path`
- 已修复：`maya_build_asset_info` 显式传 `info_path`
- 已修复：`pipeline_compare_asset` 显式传 `output_path`
- 已修复：`cache_group` 从项目配置传入

当前写法：

```json
{
  "info_path": "{{input.info_dir}}/{{input.source_path | stem}}_info.json"
}
```

```json
{
  "info_path": "{{input.info_dir}}/{{input.rig_path | stem}}_info.json"
}
```

```json
{
  "output_path": "{{input.info_dir}}/{{input.source_path | stem}}_vs_{{input.rig_path | stem}}_compare_result.json"
}
```

### 5.2 `tex_to_rig_verify_and_sync`

用途：对比 + ABC 拼装 + 材质 + 后验证 + 保存。

当前状态：

- ABC 已写入 `.info`
- `_materials.json` 已写入 `.info`
- pre/post rig info 已写入 `.info`
- pre/post compare result 已写入 `.info`

需要继续检查：

- `save_scene` 与后置 `verify` 的顺序是否符合“保存最后一步”规则
- `maya_sync_rig_incremental` 的内部临时报告或 fallback 输出是否仍可能散落到根目录或 source 同目录
- 已修复：`blender_export_abc` 不再重复生成 `_materials.json`，材质只由 `blender_extract_materials` 产出

### 5.3 `blender_tex_export`

用途：Blender 侧导出 ABC、材质、info。

当前风险：

- 已修复：ABC、`_materials.json`、`_info.json` 均通过 `{{input.info_dir}}` 显式指定
- 已修复：`cache_group` 从项目配置传入

### 5.4 `blender_to_maya_full_build`

用途：Blender source 全量构建到 Maya。

涉及：

- `blender_export_abc`
- `blender_extract_materials`
- `maya_build_mesh_from_abc`
- `maya_apply_materials`

已纳入 `.info` 规范，因为它同样生产 ABC 和材质 JSON。

## 6. 推荐目录布局

以 `mihouwang` 为例：

```text
projects/ysj/20260511_203000_mihouwang_wf-xxxx/
  ysj_chr_mihouwang_tex_texMaster_v003.blend
  ysj_chr_mihouwang_rig_rigMaster_v002.ma
  ysj_chr_mihouwang_rig_rigMaster_v008.ma
  mihouwang_wf-xxxx.md
  manifest.json
  .info/
    ysj_chr_mihouwang_tex_texMaster_v003.abc
    ysj_chr_mihouwang_tex_texMaster_v003_info.json
    ysj_chr_mihouwang_tex_texMaster_v003_materials.json
    ysj_chr_mihouwang_rig_rigMaster_v002_pre_sync.json
    ysj_chr_mihouwang_rig_rigMaster_v002_pre_compare_result.json
    ysj_chr_mihouwang_rig_rigMaster_v002_post_sync.json
    ysj_chr_mihouwang_rig_rigMaster_v002_post_compare_result.json
```

## 7. 后续改造拆分

### Phase 1: 工作流路径统一

范围：

- `workflows/tex_to_rig_verify.json`（已完成）
- `workflows/blender_tex_export.json`（已完成）
- `workflows/blender_to_maya_full_build.json`（已完成）
- `tools/verify_mcp_contract.py`（已补充静态门禁）

目标：

- 所有 JSON / ABC / materials / compare_result 显式写入 `{{input.info_dir}}`
- 只读对比链路和同步链路目录结构一致
- 静态契约扫描能抓到 workflow 中遗漏的 `.info` 输出

验证：

- `python tools/verify_mcp_contract.py`
- 纯函数模板测试覆盖 `stem`、`replace`、`{{input.info_dir}}`

### Phase 2: 默认输出策略统一

范围：

- `blender_build_asset_info`
- `maya_build_asset_info`
- `pipeline_compare_asset`
- `blender_export_abc`
- `maya_export_abc`
- `pipeline_export_abc_auto`
- `blender_extract_materials`

目标：

- workflow 显式传路径是第一优先级
- 如果未显式传路径，但 payload 有 `parameters.info_dir` 或 `extra_params.info_dir`，默认写入 `.info`
- 如果是单技能调用，默认写入任务沙盒内 `.info`
- 禁止 fallback 到服务器源文件同目录
- 已完成：上述节点均已统一默认输出策略

验证：

- 单技能 dry run 或静态构造 payload 测试
- path_guard 覆盖受保护路径
- 输出 key 只使用 `output_path`

### Phase 3: 对比契约收紧

范围：

- `pipeline_compare_asset`
- `core/asset_info_schema.py`
- `core/pairing_report.py`
- `maya_sync_rig_incremental`

目标：

- `compare_result.v1` 字段稳定
- 4 去向和 7 标签边界清晰
- sync 只消费 `pairing_groups` 和必要 source_info，不重新猜测分组
- 后置验证只阻断真实差异，不把 `ORIG_INJECT` 当失败

验证：

- `tests/test_pipeline_compare.py`
- `tests/test_rig_sync_profile.py`
- `tests/test_compare_result_contract.py`

### Phase 4: `maya_sync_rig_incremental` 专项治理

范围：

- `skills/maya_sync_rig_incremental/maya_sync_rig_incremental.py`
- 相关 helper 与测试

目标：

- 拆分 source 读取、target 采集、决策校验、执行动作、回执构造
- 每个失败路径返回明确 `ERROR` 或 `AUDIT_FAILED`
- 所有修改包裹在 Maya undo chunk
- compare_result 与当前 target 不匹配时必须撤销并阻断
- 权重 / BlendShape / 材质迁移路径可单独测试

验证：

- 单元测试覆盖 compare_result 消费
- mayapy 小场景集成测试
- mihouwang 巡航复测

### Phase 5: MCP 入口与文档同步

范围：

- `mcp_server/tools_operations.py`
- `mcp_server/models.py`
- `AGENTS.md`
- `docs/skills/cgi-pipeline-operator/SKILL.md`
- `skills/*/SKILL.md`

目标：

- Tool 描述不再宣称 JSON 位于服务器 `.info`
- `maya_sync_rig_incremental` 手写 Tool 支持 `compare_result`
- 所有说明统一为“服务器只读，机器中间产物进任务沙盒 `.info`”

验证：

- `maya_list_skills` 参数符合文档
- MCP instructions 中沙盒规则与 workflow 实现一致

## 8. 风险与红线

- 不允许写入 `X:/Project/.../pub`
- 不允许把工作流中间产物写到服务器 `.info`
- 不允许为了路径统一改动对比算法行为
- 不允许把 `compare_result` 当报告文本使用，它是机器契约
- 不允许在 `maya_sync_rig_incremental` 内吞异常继续保存
- 破坏性链路必须最终保存到沙盒，不能覆盖输入 rig

## 9. 完成标准

本专项完成需满足：

- 所有对比/拼装 workflow 的机器中间产物全部进入 `.info`
- `tools/verify_mcp_contract.py` 能静态阻断路径回退
- 只读对比和同步拼装均能生成统一 manifest
- `pipeline_compare_asset` 输出的 `compare_result` 能被 sync 稳定消费
- mihouwang 服务器源文件巡航通过，服务器源文件无写入
- 文档、SKILL.md、MCP Tool 描述三处一致
