---
description: CGI Pipeline 开发测试流程（自动执行所有命令）
---
// turbo-all

## 开发测试

1. 修改API代码
2. 运行 Blender 后台测试
3. 运行 Maya 后台测试（mayapy）
4. 查看测试输出
5. 更新 API manifest 与对应 `api_help.md`
6. 运行 `tools/sync_notes_api_index.py --write`，同步 Notes API 目录
