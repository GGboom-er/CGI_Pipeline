<#
.SYNOPSIS
CGI Pipeline v2 One-Click Installer & Runner
.DESCRIPTION
This script uses the Notes-managed Python 3.11 Conda environment, installs dependencies,
scans for Autodesk Maya installations to generate the .env file, and automatically 
boots both the Celery Worker and MCP Server.
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
Set-Location $ProjectRoot
$env:PYTHONNOUSERSITE = "1"
$PythonExe = Join-Path (Split-Path $ProjectRoot -Parent) "conda_envs\cgi_pipeline\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Canonical CGI Python not found: $PythonExe. Run bin\setup.bat first."
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
$NotesPythonPrefix = Split-Path $PythonExe -Parent
$managedPath = @(
    $NotesPythonPrefix,
    (Join-Path $NotesPythonPrefix "Library\mingw-w64\bin"),
    (Join-Path $NotesPythonPrefix "Library\usr\bin"),
    (Join-Path $NotesPythonPrefix "Library\bin"),
    (Join-Path $NotesPythonPrefix "Scripts")
) | Where-Object { Test-Path -LiteralPath $_ }
$env:PATH = (($managedPath + ($env:PATH -split [IO.Path]::PathSeparator)) |
    Where-Object { $_ } | Select-Object -Unique) -join [IO.Path]::PathSeparator
$version = (& $PythonExe -s -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null).Trim()
if ($version -ne "3.11") {
    throw "Canonical CGI Python must be 3.11: $PythonExe"
}

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "     CGI Pipeline One-Click Deployment" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

# 1. Check managed Python
Write-Host "`n[1/5] Checking Python installation..." -ForegroundColor Yellow
$PyVersion = & $PythonExe -s --version
Write-Host "Found: $PyVersion at $PythonExe" -ForegroundColor Green

# 2. Managed environment dependencies
Write-Host "`n[2/5] Checking managed environment dependencies..." -ForegroundColor Yellow
Write-Host "Installing/Updating dependencies..."
& $PythonExe -s -m pip check
if (Test-Path "requirements.txt") {
    & $PythonExe -s -m pip install -r requirements.txt -q
}
Write-Host "Dependencies installed successfully." -ForegroundColor Green

# 3. Locate Maya and configure .env
Write-Host "`n[3/5] Configuring Environment (.env)..." -ForegroundColor Yellow
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Test-Path $EnvFile)) {
    Write-Host ".env not found. Scanning for Autodesk Maya..."
    
    $MayaSearchPaths = @(
        "C:\Program Files\Autodesk",
        "D:\Program Files\Autodesk",
        "E:\Program Files\Autodesk"
    )
    
    $FoundMayaPath = $null
    foreach ($path in $MayaSearchPaths) {
        if (Test-Path $path) {
            $MayaFolders = Get-ChildItem -Path $path -Directory -Filter "Maya20*" | Sort-Object Name -Descending
            if ($MayaFolders.Count -gt 0) {
                $FoundMayaPath = $MayaFolders[0].FullName
                break
            }
        }
    }

    if ($FoundMayaPath) {
        Write-Host "Found Maya at: $FoundMayaPath" -ForegroundColor Green
        $EnvContent = @"
MAYA_LOCATION=$FoundMayaPath
PROJECT_ROOT=$ProjectRoot
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
MCP_HOST=0.0.0.0
MCP_PORT=8000
"@
        Set-Content -Path $EnvFile -Value $EnvContent
        Write-Host "Generated .env file successfully." -ForegroundColor Green
    }
    else {
        Write-Host "WARNING: Could not automatically locate Maya." -ForegroundColor Yellow
        Write-Host "Please create the .env file manually and set MAYA_LOCATION." -ForegroundColor Yellow
    }
}
else {
    Write-Host ".env already exists. Skipping auto-generation." -ForegroundColor Green
}

# 4. Start Celery Worker
Write-Host "`n[4/5] Starting Celery Worker (Background)..." -ForegroundColor Yellow
$WorkerLog = Join-Path $ProjectRoot "celery_worker.log"
$WorkerErrLog = Join-Path $ProjectRoot "celery_worker_err.log"
Start-Process -FilePath $PythonExe -ArgumentList @("-s", "-m", "celery", "-A", "core.tasks", "worker", "-P", "solo", "--loglevel=info") -WindowStyle Minimized -RedirectStandardOutput $WorkerLog -RedirectStandardError $WorkerErrLog
Write-Host "Celery Worker started. Logs are being written to celery_worker.log" -ForegroundColor Green

# 5. Start MCP Server
Write-Host "`n[5/5] Starting MCP Server..." -ForegroundColor Yellow
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "Pipeline is now ONLINE! Keep this window open." -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

& $PythonExe -s mcp_server/server.py --http
