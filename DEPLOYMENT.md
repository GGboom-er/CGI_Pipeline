# CGI Pipeline 一键部署与启动指南

不管您是新加入了团队，换了工位，还是重装了系统。只要按照以下极简 3 步，即可在 5 分钟内在新机器上启动整套管线架构。

---

## 前置准备

1. **Python 3.10+**（请确保添加到了环境变量 PATH）。
2. 已安装 **Autodesk Maya**（推荐 2024+）。
3. *无需额外部署 Redis，本库已自带嵌入式 `redis-server.exe`。*

---

## 一键傻瓜式部署

为了省去手动寻找 Maya 路径和安装依赖的折磨，我们提供了自动化部署脚本。有两种方式供您选择：

### 方式一：Conda 部署（推荐标准流程）
在根目录下运行 `bin\setup.bat`。该脚本将自动：
- 创建 `cgi_pipeline` Conda 环境。
- 安装所有必要依赖。
- 生成 `.env` 配置文件模板。

### 方式二：独立快速部署 (Virtualenv)
如果您没有 Conda，可右键单击 `deploy/install_and_run.ps1`，选择 **“使用 PowerShell 运行”**。
脚本将自动为您完成以下工作：
   - 自动检测并创建 Python 虚拟环境 (`.venv`)。
   - 自动安装 `requirements.txt` 中的所有依赖包。
   - 自动扫描您的 C 盘和 D 盘，寻找 Maya 的安装路径。
   - 自动在项目根目录生成 `.env` 环境变量文件，并填入正确的 Maya 路径。
   - 自动唤起后台 Celery Worker 监听。
   - 自动拉起 MCP Server 服务端口。

---

## 检查是否成功

部署脚本运行完毕后，只要您的终端出现：
`[cgi_pipeline_mcp] Streamable HTTP on http://0.0.0.0:8000/mcp`
即代表一切就绪。

您可以直接让 AI 连接这个 MCP 端口，或者打开浏览器访问您的 Dashboard 开始进行自动化资产处理了！

> **故障排查**：
> - 如果终端提示 `Redis connection error`，请检查您的 Redis 守护进程是否在 `localhost:6379` 运行。
> - 如果 AI Agent 无法调用 Maya 技能，请检查生成的 `.env` 文件里的 `MAYA_LOCATION` 是否指向了正确的 Maya 根目录。
