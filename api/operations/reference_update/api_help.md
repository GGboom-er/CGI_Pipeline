# API Help: maya.rig.reference.update

- 作用与场景：按项目解析器提供的旧路径到新路径映射，预览或替换 Maya 引用路径。
- 用法：`execute_api(maya.rig.reference.update, params={operation, target_map})`
- DCC：`maya`；层级：`destructive`；前台或后台均可执行。

## 参数

- `operation`: 必填，`preview` 或 `apply`；先预览，再应用。
- `target_map`: 必填数组，只允许传项目解析器生成的明确旧路径到新路径映射。
- `skip_status`: 可选，默认 `SKIP_NOT_TARGET`。

## 输出

- 引用计划/校验行、状态计数和已验证替换数量。

机器契约以 `api/maya/rig/api.yaml` 为准。
