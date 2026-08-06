# API Help: maya.asset.exec_code

- 作用与场景：在当前 Maya 会话中执行任意 Python 代码并返回结果。代码中将结果赋值给 result 变量即可。
- 用法：`execute_api(maya.asset.exec_code, params={...})`
- DCC：`maya`；层级：`destructive`；执行：`background / foreground`

## 参数
- `code`（默认 ``）：待执行的 Python 代码字符串
- `description`（默认 ``）：代码用途描述（用于审计日志）

## 输出
- 在当前 Maya 会话中执行任意 Python 代码并返回结果。代码中将结果赋值给 result 变量即可。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
