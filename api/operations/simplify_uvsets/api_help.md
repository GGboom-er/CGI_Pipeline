# API Help: maya.asset.simplify_uvsets

- 作用与场景：清理多余 UV set。对 cache 组下每个 mesh（支持绑定体 ShapeOrig 与普通体 Shape）执行直捷 UV 规整，拷贝/覆盖有效数据到 index 0 并删去其余废弃集，确保最终只保留一个名为 map1 的 UV set。破坏性操作，已包裹 undo 块。建议先用 check_uvsets 查看再执行。
- 用法：`execute_api(maya.asset.simplify_uvsets, params={...})`
- DCC：`maya`；层级：`destructive`；执行：`background / foreground`

## 参数
- `cache_group`（默认 `cache`）：操作的根组名称，默认 cache

## 输出
- 清理多余 UV set。对 cache 组下每个 mesh（支持绑定体 ShapeOrig 与普通体 Shape）执行直捷 UV 规整，拷贝/覆盖有效数据到 index 0 并删去其余废弃集，确保最终只保留一个名为 map1 的 UV set。破坏性操作，已包裹 undo 块。建议先用 check_uvsets 查看再执行。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
