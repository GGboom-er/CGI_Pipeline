# Upstream Attribution

`ue-mcp/` 是第三方上游的**独立 git 仓库**，按 Blender 侧 `dccs/blender/extensions/`
的约定收在本适配器目录内，只统一位置、不并入 CGI_Pipeline 版本树。

- Source: https://github.com/db-lyon/ue-mcp
- Version: 1.1.36 (`90c2207`)
- 采用其 C++ 插件 `UE_MCP_Bridge` 0.3.0，注册 725 个 method（61 个 handler 文件）

与 Blender 侧的区别：Blender 的 `cgi_pipeline_blender_bridge` 只吸收上游的传输思路、
代码自研；UE 侧直接运行上游的 C++ 插件，因为它提供的是蓝图图编写、材质、关卡、动画、
VFX、地形、PCG、Sequencer 等成套子系统能力，自研等于重写整个上游项目。

## 更新方式

独立仓，照常拉取上游：

```
git -C dccs/ue/extensions/ue-mcp pull
```

CGI_Pipeline 的 `.gitignore` 排除本目录下的嵌套仓，避免仓中仓。

## 调用链

```
Claude → cgi_pipeline MCP → [WebSocket] → UE_MCP_Bridge (C++) → UE
```

`dccs/ue/foreground_client.py` 读 `<uproject>/Saved/UE_MCP_Bridge/port.json`
锁文件拿端口，直连插件的原生 method。

`ue_exec_action` 的 `action` 取插件的原生 method 名，`payload` 用插件侧的原生参数名。

`node` 运行时是三套 DCC 共享的，在 `Tools/_managed/node/`，由 `NOTES_NODE_PATH`
指定；UE 侧不使用它。

## License

上游为 MIT，许可原文见 `ue-mcp/LICENSE`。本目录不复制其正文，避免与上游漂移。
