# API Help: pipeline.pipeline.resolve_asset_files

- 作用与场景：根据项目配置解析资产 source tex 文件与 target rig 文件。支持只传资产名自动查服务器最新版本，也支持传入明确文件路径直接透传。
- 用法：`execute_api(pipeline.pipeline.resolve_asset_files, params={...})`
- DCC：`pipeline`；层级：`read`；执行：`background`

## 参数
- `category`（默认 `chr`）：资产类型，如 chr/prp/env。为空时优先读取 input.category，最后回退 chr。
- `source_stage`（默认 `tex`）：source 侧阶段，默认 tex。
- `source_task`（默认 ``）：source 侧 task；为空时使用项目配置中的 primary_task。
- `source_extensions`（默认 `.blend`）：source 侧允许扩展名，逗号分隔。
- `rig_stage`（默认 `rig`）：target rig 阶段，默认 rig。
- `rig_task`（默认 ``）：target rig task；为空时使用项目配置中的 primary_task。
- `rig_extensions`（默认 `.ma,.mb`）：target rig 允许扩展名，逗号分隔。

## 输出
- 根据项目配置解析资产 source tex 文件与 target rig 文件。支持只传资产名自动查服务器最新版本，也支持传入明确文件路径直接透传。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
