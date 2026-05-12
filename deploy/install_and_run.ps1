<#
.SYNOPSIS
CGI Pipeline v2 One-Click Installer & Runner
.DESCRIPTION
This script sets up the Python virtual environment, installs dependencies, 
scans for Autodesk Maya installations to generate the .env file, and automatically 
boots both the Celery Worker and MCP Server.
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
Set-Location $ProjectRoot

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "     CGI Pipeline One-Click Deployment" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

# 1. Check Python
Write-Host "`n[1/5] Checking Python installation..." -ForegroundColor Yellow
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Python is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.10+ and try again." -ForegroundColor Red
    Exit
}
$PyVersion = python --version
Write-Host "Found: $PyVersion" -ForegroundColor Green

# 2. Virtual Environment & Dependencies
Write-Host "`n[2/5] Setting up Virtual Environment..." -ForegroundColor Yellow
$VenvPath = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $VenvPath)) {
    Write-Host "Creating new virtual environment at .venv..."
    python -m venv .venv
}
else {
    Write-Host "Virtual environment already exists." -ForegroundColor Green
}

Write-Host "Installing/Updating dependencies..."
& "$VenvPath\Scripts\python.exe" -m pip install --upgrade pip -q
if (Test-Path "requirements.txt") {
    & "$VenvPath\Scripts\python.exe" -m pip install -r requirements.txt -q
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
Start-Process -FilePath "$VenvPath\Scripts\python.exe" -ArgumentList "-m celery -A core.tasks worker -P solo --loglevel=info" -WindowStyle Minimized -RedirectStandardOutput $WorkerLog -RedirectStandardError $WorkerErrLog
Write-Host "Celery Worker started. Logs are being written to celery_worker.log" -ForegroundColor Green

# 5. Start MCP Server
Write-Host "`n[5/5] Starting MCP Server..." -ForegroundColor Yellow
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "Pipeline is now ONLINE! Keep this window open." -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

& "$VenvPath\Scripts\python.exe" mcp_server/server.py --http
