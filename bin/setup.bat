@echo off
chcp 65001 >nul
echo ============================================
echo   CGI Pipeline — 一键部署脚本
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

REM ── 使用 Notes 受管 brain prefix 创建/更新 CGI Python 运行时──
for %%I in ("%~dp0..\..\..\..\..\.conda_envs\brain") do set "CGI_ENV=%%~fI"
set "PATH=%CGI_ENV%;%CGI_ENV%\Library\mingw-w64\bin;%CGI_ENV%\Library\usr\bin;%CGI_ENV%\Library\bin;%CGI_ENV%\Scripts;%PATH%"
set "PYTHONNOUSERSITE=1"
echo.
echo [STEP 1] 创建/更新共享 brain Python 3.11 基座...
conda env create --prefix "%CGI_ENV%" -f environment.yml -y 2>nul
if errorlevel 1 (
    echo [INFO] 环境已存在，执行更新...
    conda env update --prefix "%CGI_ENV%" -f environment.yml --prune -y
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
call "%CGI_ENV%\python.exe" -s --version
call "%CGI_ENV%\python.exe" -s -m pip check
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
echo   set PYTHONPATH=%PROJECT_ROOT%\src
echo   "%CGI_ENV%\python.exe" -m cgi_pipeline.server.server --http
echo ============================================
popd
pause
