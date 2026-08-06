# API Help: blender.asset.blender_exec_code

- 作用与场景：在 Blender Python 环境中执行任意代码并返回结果。支持后台无头模式和当前 Blender 界面前台模式；代码中将结果赋值给 result 变量。
- 用法：`execute_api(blender.asset.blender_exec_code, params={...})`
- DCC：`blender`；层级：`destructive`；执行：`background / foreground`

## 参数
- `code`（默认 ``）：待执行的 Python 代码字符串
- `description`（默认 ``）：代码用途描述（用于审计日志）

## 输出
- 在 Blender Python 环境中执行任意代码并返回结果。支持后台无头模式和当前 Blender 界面前台模式；代码中将结果赋值给 result 变量。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
