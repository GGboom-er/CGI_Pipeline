# setup_defender_exclusion.ps1
# ── CGI Pipeline v2.0 — 缺陷2修正：Windows Defender 扫描豁免 ──
# ⚠ 必须以管理员权限运行此脚本
# 用法：powershell -ExecutionPolicy Bypass -File setup_defender_exclusion.ps1

# 从 .env 读取 PROJECT_ROOT，或使用脚本所在目录
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $scriptDir ".env"

if (Test-Path $envFile) {
    $projectRoot = (Get-Content $envFile | Where-Object { $_ -match "^PROJECT_ROOT=" }) -replace "^PROJECT_ROOT=", ""
    $projectRoot = $projectRoot.Trim()
    Write-Host "[INFO] 从 .env 读取 PROJECT_ROOT: $projectRoot" -ForegroundColor Cyan
} else {
    $projectRoot = $scriptDir
    Write-Host "[INFO] 未找到 .env，使用脚本目录: $projectRoot" -ForegroundColor Yellow
}

$ipcPath = Join-Path $projectRoot "ipc"
$sandboxPath = Join-Path $projectRoot "sandbox"

# 将 IPC 和沙盒目录加入 Windows Defender 排除列表
Add-MpPreference -ExclusionPath $ipcPath, $sandboxPath

# 验证排除路径已生效
$exclusions = (Get-MpPreference).ExclusionPath
Write-Host "=== Windows Defender Exclusion Paths ===" -ForegroundColor Cyan

$required = @($ipcPath, $sandboxPath)
$allPassed = $true

foreach ($path in $required) {
    if ($exclusions -contains $path) {
        Write-Host "  [PASS] $path" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $path — 未找到排除项！" -ForegroundColor Red
        $allPassed = $false
    }
}

if ($allPassed) {
    Write-Host "`nPASS: Windows Defender 扫描豁免配置完成" -ForegroundColor Green
} else {
    Write-Host "`nFAIL: 请以管理员权限重新运行此脚本" -ForegroundColor Red
    exit 1
}
