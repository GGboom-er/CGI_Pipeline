# API Help: maya.asset.check_uvsets

- 作用与场景：只读扫描 cache 组下所有 mesh 的 UV set 状况，并标记需要清理的 mesh；建议在执行 simplify_uvsets 之前先运行此 API。
- 用法：`execute_api(maya.asset.check_uvsets, params={...})`
- DCC：`maya`；层级：`read`；执行：`background / foreground`

## 参数
- `cache_group`（默认 `cache`）：扫描的根组名称，默认 cache

## 输出
- 返回每个 mesh 的 UV 集数量、有效性和空壳集标记。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
