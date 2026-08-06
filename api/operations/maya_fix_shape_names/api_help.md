# API Help: maya.rig.maya_fix_shape_names

- 作用与场景：规范化网格 Shape 的名称为 [模型名]Shape，将绑定或变形原始形重命名为 [模型名]ShapeOrig，并严格拔除没有任何连接的残渣死形节点。
- 用法：`execute_api(maya.rig.maya_fix_shape_names, params={...})`
- DCC：`maya`；层级：`destructive`；执行：`background / foreground`

## 参数
- `target_group`（默认 ``）：要限制检查的特定组名（如 |Group|Geometry），为空则强制检查全场景网格

## 输出
- 规范化网格 Shape 的名称为 [模型名]Shape，将绑定或变形原始形重命名为 [模型名]ShapeOrig，并严格拔除没有任何连接的残渣死形节点。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
