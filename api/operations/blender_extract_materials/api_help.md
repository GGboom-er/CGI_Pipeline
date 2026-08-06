# API Help: blender.asset.blender_extract_materials

- 作用与场景：从 Blender 场景 cache 组采集最终 Color/Alpha 与 per-face 对应关系；仅 UDIM 输入按 UV 象限拆分，输出 schema v3 _materials.json。与 maya_apply_materials 配对。
- 用法：`execute_api(blender.asset.blender_extract_materials, params={...})`
- DCC：`blender`；层级：`write`；执行：`background / foreground`

## 参数
- `output_path`（默认 ``）：_materials.json 输出完整路径。workflow 中必须传入任务沙盒 .info 路径；为空时从任务沙盒自动推导。
- `cache_group`（必填）：必填。要采集的 Blender 根对象名，由项目配置或 workflow 传入。

## 输出
- 从 Blender 场景 cache 组采集最终 Color/Alpha 与 per-face 对应关系；仅 UDIM 输入按 UV 象限拆分，输出 schema v3 _materials.json。与 maya_apply_materials 配对。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
