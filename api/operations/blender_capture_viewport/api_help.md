# API Help: blender.asset.blender_capture_viewport

- 作用与场景：获取 Blender 当前活动视口或后台渲染截图，并保存为 PNG。
- 用法：`execute_api(blender.asset.blender_capture_viewport, params={...})`
- DCC：`blender`；层级：`read`；执行：`background / foreground`

## 参数
- `width`（默认 `1920`）：截图宽度
- `height`（默认 `1080`）：截图高度

## 输出
- 获取 Blender 当前活动视口或后台渲染截图，并保存为 PNG。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
