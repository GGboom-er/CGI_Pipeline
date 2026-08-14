# Upstream Attribution

`ue-mcp/` 是第三方上游的**独立 git 仓库**，按 Blender 侧 `integrations/blender/extensions/`
的约定收在本适配器目录内，只统一位置、不并入 CGI_Pipeline 版本树。

- Source: https://github.com/db-lyon/ue-mcp
- Version: 1.1.36 (`90c2207`)
- 采用其 C++ 插件 `UE_MCP_Bridge` 0.3.0，注册 725 个 method（61 个 handler 文件）

与 Blender 侧一致：两者都直接使用上游完整实现。UE 使用其 C++ 插件，因为它提供蓝图图
编写、材质、关卡、动画、VFX、地形、PCG、Sequencer 等成套子系统能力。

## 更新方式

独立仓，照常拉取上游：

```
git -C integrations/ue/extensions/ue-mcp pull
```

CGI_Pipeline 的 `.gitignore` 排除本目录下的嵌套仓，避免仓中仓。

## 调用链

```
Claude → cgi_pipeline MCP → [WebSocket] → UE_MCP_Bridge (C++) → UE
```

`src/cgi_pipeline/hosts/ue/foreground_client.py` 读 `<uproject>/Saved/UE_MCP_Bridge/port.json`
锁文件拿端口，直连插件的原生 method。

通过 `execute_api(ue.editor.action.invoke, params={port, method, arguments})` 调用原生 method；`method` 使用插件的原生 method 名，`arguments` 使用插件侧的原生参数名。

`node` 运行时是三套 DCC 共享的，在 `Tools/_managed/node/`，由 `NOTES_NODE_PATH`
指定；UE 侧不使用它。

## License

上游为 MIT，许可原文见 `ue-mcp/LICENSE`。本目录不复制其正文，避免与上游漂移。
