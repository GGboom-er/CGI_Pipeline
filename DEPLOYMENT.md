# CGI Pipeline 一键部署与启动指南

不管您是新加入了团队，换了工位，还是重装了系统。只要按照以下极简 3 步，即可在 5 分钟内在新机器上启动整套管线架构。

---

## 前置准备

1. **Miniconda / Anaconda**；CGI 使用 Notes 受管 Python 3.11 运行时：`Y:/GGbommer/scripts/.conda_envs/brain`。
2. 已安装 **Autodesk Maya 2025**（Python 3.11 / PySide6）。
3. *无需额外安装 Redis；Notes 已统一管理唯一的 CGI Redis 运行时。*

---

## 一键傻瓜式部署

为了省去手动寻找 Maya 路径和安装依赖的折磨，我们提供了自动化部署脚本。有两种方式供您选择：

### 方式一：Conda 部署（推荐标准流程）
在根目录下运行 `bin\setup.bat`。该脚本将自动：
- 在 `Y:/GGbommer/scripts/.conda_envs/brain` 创建/更新共享大脑 Python 基座。
- 安装所有必要依赖。
- 生成 `.env` 配置文件模板。

### 启动脚本
`deploy/install_and_run.ps1` 只接受上述 Notes 受管 Python 3.11 prefix，不读取用户目录或当前激活环境；缺少该环境时先运行 `bin\setup.bat`。

### Maya 宿主 SDK

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File deploy/maya/install.ps1 Install
$env:MAYA_MODULE_PATH = [Environment]::GetEnvironmentVariable('MAYA_MODULE_PATH', 'User')
& 'C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe' deploy/maya/doctor.py
```

安装器只管理用户级 `MAYA_MODULE_PATH` 中的 CGI module 目录，不写全局 `PYTHONPATH`。安装后重启 Maya；doctor 应返回 `status=PASS`。

HTTP 服务入口在仓库内移动后，使用 `bin/manage_mcp_http.ps1 restart` 接管并切换。管理器只在端口、PID、创建时间、命令行、仓库路径和规范 Python 全部匹配时替换原入口；普通 `stop` 仍拒绝路径不匹配的进程。

---

## 检查是否成功

部署脚本运行完毕后，只要您的终端出现：
`[cgi_pipeline_mcp] Streamable HTTP on http://127.0.0.1:8000/mcp`
即代表一切就绪。

您可以直接让 AI 连接这个 MCP 端口，或者打开浏览器访问您的 Dashboard 开始进行自动化资产处理了！

> **故障排查**：
> - 如果终端提示 `Redis connection error`，请检查您的 Redis 守护进程是否在 `localhost:6379` 运行。
> - 如果 AI Agent 无法调用 Maya API，请检查生成的 `.env` 文件里的 `MAYA_LOCATION` 是否指向了正确的 Maya 根目录，并先用 `list_apis` / `api_help` 确认入口与参数。
