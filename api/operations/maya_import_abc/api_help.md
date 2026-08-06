# API Help: maya.asset.maya_import_abc

- 作用与场景：将 Alembic (.abc) 文件导入 Maya 场景，支持坐标系缩放对齐（默认 100x Blender→Maya）并冻结变换。
- 用法：`execute_api(maya.asset.maya_import_abc, params={...})`
- DCC：`maya`；层级：`write`；执行：`background / foreground`

## 参数
- `abc_path`（默认 ``）：ABC 文件的完整路径（必填）
- `scale_factor`（默认 `100.0`）：导入后缩放系数，0 表示不缩放
- `new_scene`（默认 `True`）：是否先新建空场景再导入

## 输出
- 将 Alembic (.abc) 文件导入 Maya 场景，支持坐标系缩放对齐（默认 100x Blender→Maya）并冻结变换。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
