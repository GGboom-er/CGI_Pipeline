# API Help: maya.asset.maya_check_textures

- 作用与场景：扫描 Maya 场景中所有 file 节点，检查贴图是否存在于磁盘（支持 UDIM），按目录分组汇总报告。
- 用法：`execute_api(maya.asset.maya_check_textures, params={...})`
- DCC：`maya`；层级：`read`；执行：`background / foreground`

## 参数
- `check_exists`（默认 `True`）：是否检查贴图文件在磁盘上是否存在

## 输出
- 扫描 Maya 场景中所有 file 节点，检查贴图是否存在于磁盘（支持 UDIM），按目录分组汇总报告。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
