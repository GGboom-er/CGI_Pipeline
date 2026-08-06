# API Help: maya.asset.maya_build_mesh_from_abc

- 作用与场景：使用 PyAlembic 读取 ABC 拓扑数据，在 Maya 中用 OpenMaya MFnMesh.create 纯数据构建 mesh（含 UV），还原 DAG 层级。不走 Maya import 命令。支持全量构建和按 mesh_filter 过滤构建。
- 用法：`execute_api(maya.asset.maya_build_mesh_from_abc, params={...})`
- DCC：`maya`；层级：`write`；执行：`background / foreground`

## 参数
- `abc_path`（默认 ``）：ABC 文件绝对路径（必填）
- `mesh_filter`（默认 `None`）：DAG 路径列表，只构建匹配项。不传 = 全量构建
- `parent_group`（默认 ``）：构建后的顶层组名。默认还原 ABC 原始层级

## 输出
- 使用 PyAlembic 读取 ABC 拓扑数据，在 Maya 中用 OpenMaya MFnMesh.create 纯数据构建 mesh（含 UV），还原 DAG 层级。不走 Maya import 命令。支持全量构建和按 mesh_filter 过滤构建。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
