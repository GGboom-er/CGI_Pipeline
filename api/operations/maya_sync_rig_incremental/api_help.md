# API Help: maya.rig.maya_sync_rig_incremental

- 作用与场景：在 target rig 场景中，依据前置 compare_result 和 source ABC 增量重建/更新 mesh（纯几何）。注入/重建时传 UV 与可用显式法线，面序固定对齐 Maya AbcImport，不猜测几何外侧。
- 用法：`execute_api(maya.rig.maya_sync_rig_incremental, params={...})`
- DCC：`maya`；层级：`destructive`；执行：`background / foreground`

## 参数
- `compare_result`（默认 ``）：必填。maya_compare_asset_in_scene 或 pipeline_compare_asset 输出的 compare_result.json 路径；workflow 内也可直接传 output.compare_result 字典。sync 只消费其中 pairing_groups，不重新对比。
- `source_abc`（默认 ``）：source 侧 ABC 文件路径。source 独有 mesh 需要完整拓扑，推荐传 ABC。
- `source_info`（默认 ``）：source 侧 _info.json 路径。无 ABC 时的降级路径，仅能更新已有 mesh，不能可靠创建 source 独有 mesh。
- `cache_group`（默认 `cache`）：target rig 几何根组。workflow 应从项目配置传入。
- `dry_run`（默认 `False`）：兼容旧调用参数。只看差异请使用 maya_compare_asset_in_scene，本API执行真实拼装。

## 输出
- 在 target rig 场景中，依据前置 compare_result 和 source ABC 增量重建/更新 mesh（纯几何）。注入/重建时传 UV 与可用显式法线，面序固定对齐 Maya AbcImport，不猜测几何外侧。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
