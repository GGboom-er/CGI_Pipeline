# API Help: maya.asset.maya_export_abc

- 作用与场景：将当前 Maya 场景导出为 Alembic (.abc)，含 UV、FaceSet、可见性，Ogawa 格式。不保留 skinCluster。
- 用法：`execute_api(maya.asset.maya_export_abc, params={...})`
- DCC：`maya`；层级：`write`；执行：`background / foreground`

## 参数
- `abc_path`（默认 ``）：ABC 输出完整路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。
- `frame_range`（默认 `[1, 1]`）：导出帧范围 [start, end]
- `root_nodes`（默认 ``）：要导出的根节点列表。为空则导出全部顶层。

## 输出
- 将当前 Maya 场景导出为 Alembic (.abc)，含 UV、FaceSet、可见性，Ogawa 格式。不保留 skinCluster。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
