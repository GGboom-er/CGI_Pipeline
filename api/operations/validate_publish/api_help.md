# API Help: maya.asset.validate_publish

- 作用与场景：发布前自动化 QC 检查：验证场景完整性、cache 组存在性、mesh 数量、未知节点、空组、命名规范。只读操作，输出 PASS/FAIL 报告。
- 用法：`execute_api(maya.asset.validate_publish, params={...})`
- DCC：`maya`；层级：`read`；执行：`background / foreground`

## 参数
- `project`（默认 ``）：项目代号，为空则从数据流推断
- `category`（默认 ``）：资产分类，为空则从数据流推断
- `asset_name`（默认 ``）：资产名称，为空则从数据流推断
- `stage`（默认 ``）：制作阶段，为空则从数据流推断
- `cache_group`（默认 ``）：cache 组名称，为空则自动查找

## 输出
- 发布前自动化 QC 检查：验证场景完整性、cache 组存在性、mesh 数量、未知节点、空组、命名规范。只读操作，输出 PASS/FAIL 报告。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
