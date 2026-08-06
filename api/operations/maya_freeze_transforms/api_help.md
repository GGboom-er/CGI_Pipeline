# API Help: maya.asset.maya_freeze_transforms

- 作用与场景：冻结场景中所有可变换节点的 Translate/Rotate/Scale，并清除构造历史。
- 用法：`execute_api(maya.asset.maya_freeze_transforms, params={...})`
- DCC：`maya`；层级：`destructive`；执行：`background / foreground`

## 参数
- `target_nodes`（默认 `[]`）：指定节点列表，为空则作用于所有 mesh/transform

## 输出
- 冻结场景中所有可变换节点的 Translate/Rotate/Scale，并清除构造历史。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
