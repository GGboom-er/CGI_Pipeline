# 历史手工脚本归档

本目录保存旧调试脚本和手工验证脚本，只用于追溯。

归档原因：
- 依赖已废弃的 `skills/registry.json`。
- 调用已移除的旧 `compare_asset` 入口。
- 写入或读取旧 `runs/assets` 目录结构。
- 使用旧 Dashboard `/api/execute_graph` 或旧技能短名。

当前验证入口见 `tests/README.md`。归档脚本重新启用前，必须先改造为当前 MCP、任务沙盒、`.info` 和 `receipt.outputs` 契约。
