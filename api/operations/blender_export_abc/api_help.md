# API Help: blender.asset.blender_export_abc

- 作用与场景：选中指定几何根组，禁用修改器，导出 Alembic (.abc)。只管 ABC 几何导出，不生成 _info.json 或 _materials.json。
- 用法：`execute_api(blender.asset.blender_export_abc, params={...})`
- DCC：`blender`；层级：`write`；执行：`background / foreground`

## 参数
- `abc_path`（默认 ``）：ABC 输出完整路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。
- `cache_group`（默认 ``）：要导出的组名（为空则从项目推断即可）

## 输出
- 选中指定几何根组，禁用修改器，导出 Alembic (.abc)。只管 ABC 几何导出，不生成 _info.json 或 _materials.json。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
