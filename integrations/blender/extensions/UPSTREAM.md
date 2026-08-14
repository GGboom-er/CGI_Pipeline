# Blender MCP Upstream

`blender_mcp/` is the sole Blender foreground transport for CGI Pipeline. It
provides the upstream addon, MCP server, tool code, and API reference as a
local third-party support library. It is not part of the CGI Pipeline Git
history.

- Source: https://projects.blender.org/lab/blender_mcp.git
- Verified commit: `4309a39646e644261624bfcd2bca669b343b7621`
- License: GPL-3.0-or-later (upstream SPDX headers and addon manifest)
- Local addon declaration: Blender 5.0.0 or newer
- Runtime bridge: addon TCP server, default `127.0.0.1:9876`

## Verified Runtime Range

On the managed Notes Python 3.11 environment with Blender 5.0.1, the addon
registers, the background bridge listens, the official MCP server completes
its handshake, and `get_objects_summary` returns live scene data. Code
execution returns `result`, stdout, stderr, and a full traceback on failure.

## Update

```powershell
git -C integrations/blender/extensions/blender_mcp pull
```

CGI remains the only user-facing MCP endpoint. Its Blender foreground adapter
uses the upstream `send_code` client, turns upstream `status: error` responses
into CGI `ERROR` receipts, and retains stdout, stderr, and traceback fields.
