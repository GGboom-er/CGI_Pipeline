# API Help: pipeline.pipeline.copy_files

- 作用与场景：通用文件拷贝：source → destination，文件或目录均可。纯文件系统操作，不启动任何 DCC。
- 用法：`execute_api(pipeline.pipeline.copy_files, params={...})`
- DCC：`pipeline`；层级：`write`；执行：`background`

## 参数
- `source`（默认 ``）：源路径（文件或目录的完整路径）
- `destination`（默认 ``）：目标路径（文件或目录的完整路径）
- `overwrite`（默认 `False`）：目标已存在时是否覆盖

## 输出
- 通用文件拷贝：source → destination，文件或目录均可。纯文件系统操作，不启动任何 DCC。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
