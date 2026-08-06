# API Help: maya.rig.maya_fix_asset_hierarchy

- 作用与场景：消费 maya_check_asset_hierarchy 的检查结果，按检查结果把旧绑定几何根归一化为 |Group|Geometry|RIG_geo，并只删除安全空顶层节点。
- 用法：`execute_api(maya.rig.maya_fix_asset_hierarchy, params={...})`
- DCC：`maya`；层级：`destructive`；执行：`background / foreground`

## 参数
- `check_result`（必填）：必填；maya_check_asset_hierarchy 的 output.result。fix 只消费其中 required_root、legacy_geo_roots、candidate_source_roots、safe_delete_top_nodes。workflow 模板中写 {{outputs.check_hierarchy_pre.result}}。
- `remove_empty_source`（默认 `True`）：迁移后是否删除已经为空的源 cache 根。
- `delete_extra_top_nodes`（默认 `False`）：是否删除检查结果标记为 safe_delete_top_nodes 的空顶层节点；非空业务根永不自动删除。

## 输出
- 消费 maya_check_asset_hierarchy 的检查结果，按检查结果把旧绑定几何根归一化为 |Group|Geometry|RIG_geo，并只删除安全空顶层节点。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
