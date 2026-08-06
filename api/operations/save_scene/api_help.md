# API Help: maya.asset.save_scene

- 作用与场景：保存当前任务沙盒中的 Maya 场景。后台 pipeline 默认保存当前已打开的沙盒副本，禁止写入沙盒外路径。
- 用法：`execute_api(maya.asset.save_scene, params={...})`
- DCC：`maya`；层级：`write`；执行：`background / foreground`

## 参数
- `save_path`（默认 ``）：可选。为空时保存当前已打开的沙盒场景；若填写，只允许任务沙盒内路径。

## 输出
- 保存当前任务沙盒中的 Maya 场景。后台 pipeline 默认保存当前已打开的沙盒副本，禁止写入沙盒外路径。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
