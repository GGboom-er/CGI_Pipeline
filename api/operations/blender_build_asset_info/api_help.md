# API Help: blender.asset.blender_build_asset_info

- 作用与场景：遍历指定 Blender 几何根组下所有 mesh，采集拓扑指纹（顶点数+世界空间坐标），输出标准 _info.json。只负责几何信息，不采集贴图或面级材质。
- 用法：`execute_api(blender.asset.blender_build_asset_info, params={...})`
- DCC：`blender`；层级：`read`；执行：`background / foreground`

## 参数
- `info_path`（默认 ``）：_info.json 输出完整路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。
- `cache_group`（必填）：必填。要采集的 Blender 根对象名，由项目配置或 workflow 传入。

## 输出
- 遍历指定 Blender 几何根组下所有 mesh，采集拓扑指纹（顶点数+世界空间坐标），输出标准 _info.json。只负责几何信息，不采集贴图或面级材质。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
