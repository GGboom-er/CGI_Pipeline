# API Help: maya.asset.rename_asset

- 作用与场景：将当前场景按管线命名规范重命名并另存为。格式: {project}_{category}_{asset}_{stage}_{task}_v{version}.ma。不修改场景内容。
- 用法：`execute_api(maya.asset.rename_asset, params={...})`
- DCC：`maya`；层级：`write`；执行：`background / foreground`

## 参数
- `project`（默认 ``）：项目代号，为空则从数据流推断
- `category`（默认 ``）：资产分类，为空则从数据流推断
- `asset_name`（默认 ``）：资产名称，为空则从数据流推断
- `stage`（默认 ``）：制作阶段，为空则从数据流推断
- `task`（默认 ``）：子任务名（可选）
- `output_dir`（默认 ``）：输出目录（可选，默认与源文件同目录）

## 输出
- 将当前场景按管线命名规范重命名并另存为。格式: {project}_{category}_{asset}_{stage}_{task}_v{version}.ma。不修改场景内容。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
