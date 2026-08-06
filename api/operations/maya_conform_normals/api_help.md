# API Help: maya.asset.maya_conform_normals

- 作用与场景：对 cache/geo 组内所有 mesh 执行 polyNormal conform（normalMode=2, userNormalMode=0, ch=0），统一法线方向并清除顶点局部空间的 pnts 偏移值。
- 用法：`execute_api(maya.asset.maya_conform_normals, params={...})`
- DCC：`maya`；层级：`destructive`；执行：`background / foreground`

## 参数
- `geo_group`（默认 ``）：目标组名（默认自动查找 geo/Group/cache）

## 输出
- 对 cache/geo 组内所有 mesh 执行 polyNormal conform（normalMode=2, userNormalMode=0, ch=0），统一法线方向并清除顶点局部空间的 pnts 偏移值。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
