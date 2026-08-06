# API Help: maya.rig.maya_clean_skinweights

- 作用与场景：移除蒙皮权重中的微量噪声（低于阈值），规范化权重总和为 1.0。需要提供包含蒙皮网格的源场景文件。
- 用法：`execute_api(maya.rig.maya_clean_skinweights, params={...})`
- DCC：`maya`；层级：`destructive`；执行：`background / foreground`

## 参数
- `threshold`（默认 `0.001`）：噪声阈值，低于此值的权重将被清零

## 输出
- 移除蒙皮权重中的微量噪声（低于阈值），规范化权重总和为 1.0。需要提供包含蒙皮网格的源场景文件。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
