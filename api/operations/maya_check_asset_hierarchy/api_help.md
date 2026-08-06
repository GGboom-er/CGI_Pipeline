# API Help: maya.rig.maya_check_asset_hierarchy

- 作用与场景：纯 QC 检查：从项目配置读取当前 stage 的标准几何根，确认标准根存在、标准根下有有效 mesh，并输出层级修复节点需要消费的只读事实。
- 用法：`execute_api(maya.rig.maya_check_asset_hierarchy, params={...})`
- DCC：`maya`；层级：`read`；执行：`background / foreground`

## 参数
- `stage`（默认 `rig`）：项目阶段。用于读取 config/{project}_config.json 中 stages[stage].geom_roots[0] 作为标准根。
- `phase`（默认 `post_sync`）：检查阶段。pre_sync 要求存在可供同步读取的旧绑定几何根；post_sync 要求标准 cache 根存在并有 mesh。
- `block_on_fail`（默认 `True`）：检查失败时是否返回 AUDIT_FAILED。false 时仍返回 SUCCESS，但 output.result.passed=false。
- `block_extra_top_nodes`（默认 `False`）：是否把非空额外顶层节点作为阻断项。默认 false，仅输出风险事实，避免误阻断或误删绑定系统。

## 输出
- 纯 QC 检查：从项目配置读取当前 stage 的标准几何根，确认标准根存在、标准根下有有效 mesh，并输出层级修复节点需要消费的只读事实。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
