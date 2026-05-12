@echo off
chcp 65001 >nul
echo ============================================
echo   CGI Pipeline v2.0 — 一键部署脚本
echo ============================================
echo.

REM ── 定位项目根目录（bin/ 的上一级）──
set "PROJECT_ROOT=%~dp0.."
pushd "%PROJECT_ROOT%"

REM ── 检查 conda 是否可用 ──
where conda >nul 2>&1
if errorlevel 1 (
    echo [FAIL] 未检测到 conda，请先安装 Miniconda:
    echo        https://docs.anaconda.com/miniconda/
    popd
    exit /b 1
)
echo [PASS] conda 已安装

REM ── 使用 environment.yml 创建/更新环境 ──
echo.
echo [STEP 1] 创建 conda 环境 cgi_pipeline (Python 3.11)...
conda env create -f environment.yml -y 2>nul
if errorlevel 1 (
    echo [INFO] 环境已存在，执行更新...
    conda env update -f environment.yml --prune -y
    if errorlevel 1 (
        echo [FAIL] 环境更新失败！
        popd
        exit /b 1
    )
)
echo [PASS] Conda 环境就绪

REM ── 创建运行时目录 ──
echo.
echo [STEP 2] 创建运行时目录结构...
if not exist "ipc\cmd" mkdir "ipc\cmd"
if not exist "ipc\result" mkdir "ipc\result"
if not exist "sandbox" mkdir "sandbox"
if not exist "publish" mkdir "publish"
if not exist "audit" mkdir "audit"
echo [PASS] 目录结构就绪

REM ── 创建 .env（如果不存在）──
echo.
echo [STEP 3] 检查环境变量配置...
if not exist ".env" (
    copy ".env.example" ".env"
    echo [WARN] 已从模板创建 .env，请编辑其中的路径配置！
) else (
    echo [PASS] .env 已存在
)

REM ── 验证环境 ──
echo.
echo [STEP 4] 验证环境...
call conda run -n cgi_pipeline --no-banner python --version
call conda run -n cgi_pipeline --no-banner pip check
if errorlevel 1 (
    echo [WARN] 存在依赖冲突，请检查！
)

echo.
echo ============================================
echo   部署完成！
echo   请确认以下事项：
echo   1. 编辑 .env 中的路径配置
echo   2. 安装并启动 Memurai (Redis)
echo   3. 确认 Maya 2025 已安装
echo.
echo   启动命令：
echo   conda activate cgi_pipeline
echo   python -m mcp_server.server
echo ============================================
popd
pause
