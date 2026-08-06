# API Help: maya.asset.maya_compare_asset_in_scene

- 作用与场景：在当前已打开的 target rig Maya 场景内采集指定 cache_group 的 ShapeOrig 几何信息，读取 source 侧 ABC 或 _info.json，调用统一 compare 算法并写出 compare_result.json。用于只看差异或为后续 maya_sync_rig_incremental 提供拼装决策。
- 用法：`execute_api(maya.asset.maya_compare_asset_in_scene, params={...})`
- DCC：`maya`；层级：`read`；执行：`background / foreground`

## 参数
- `input_source`（默认 ``）：source 侧 ABC 或 _info.json。新 workflow 推荐传 blender_export_abc 的 output_path。
- `source_abc`（默认 ``）：兼容参数：source 侧 ABC。input_source 为空时使用。
- `source_info`（默认 ``）：兼容参数：source 侧 _info.json。input_source/source_abc 为空时使用。
- `cache_group`（必填）：必填。当前 Maya target rig 场景中的几何根组，由项目配置或 workflow 传入。
- `output_path`（默认 ``）：compare_result.json 输出路径。为空时从任务沙盒 .info 自动推导。
- `label_source`（默认 ``）：source 标签，空值时从输入路径自动推断。
- `label_target`（默认 `rig`）：target 标签，默认 rig。

## 输出
- 在当前已打开的 target rig Maya 场景内采集指定 cache_group 的 ShapeOrig 几何信息，读取 source 侧 ABC 或 _info.json，调用统一 compare 算法并写出 compare_result.json。用于只看差异或为后续 maya_sync_rig_incremental 提供拼装决策。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
