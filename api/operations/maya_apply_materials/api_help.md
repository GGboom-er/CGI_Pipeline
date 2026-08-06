# API Help: maya.asset.maya_apply_materials

- 作用与场景：消费 blender_extract_materials 导出的 schema v3 Color/Alpha _materials.json，为 Maya 场景中的 mesh 按面新建共享 lambert 材质球并赋予。
- 用法：`execute_api(maya.asset.maya_apply_materials, params={...})`
- DCC：`maya`；层级：`write`；执行：`background / foreground`

## 参数
- `materials_path`（默认 ``）：_materials.json 文件路径（必填）
- `target_group`（默认 ``）：限定处理范围的组名，为空则处理全场景

## 输出
- 消费 blender_extract_materials 导出的 schema v3 Color/Alpha _materials.json，为 Maya 场景中的 mesh 按面新建共享 lambert 材质球并赋予。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
