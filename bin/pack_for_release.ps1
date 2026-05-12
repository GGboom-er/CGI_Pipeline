# pack_for_release.ps1 - 一键打包发布脚本
$ErrorActionPreference = 'Stop'

$ProjectRoot = "$PSScriptRoot\.."
$ReleaseDir = "$ProjectRoot\..\CGI_Pipeline_Release"
$ReleaseZip = "$ProjectRoot\..\CGI_Pipeline_Release.zip"

Write-Host "开始打包 CGI Pipeline..." -ForegroundColor Cyan

# 如果已存在临时打包目录，直接清理
if (Test-Path $ReleaseDir) { Remove-Item $ReleaseDir -Recurse -Force }
if (Test-Path $ReleaseZip) { Remove-Item $ReleaseZip -Force }

# 创建组装目录
New-Item -ItemType Directory -Path $ReleaseDir | Out-Null

# 定义需要拷贝的核心目录（剔除垃圾和本地数据）
$IncludePaths = @(
    "bin", "config", "core", "dccs", "docs", "mcp_server", "redis_server", "skills", "tests", "dashboard",
    ".env.example", ".gitignore", "README.md", "environment.yml"
)

foreach ($item in $IncludePaths) {
    if (Test-Path "$ProjectRoot\$item") {
        Copy-Item -Path "$ProjectRoot\$item" -Destination "$ReleaseDir" -Recurse -Force
    }
}

# 压缩为 Zip（注意：这依赖于 Powershell 5.0+）
Write-Host "正在压缩到: $ReleaseZip"
Compress-Archive -Path "$ReleaseDir\*" -DestinationPath $ReleaseZip -Force

# 清除缓存临时文件
Remove-Item $ReleaseDir -Recurse -Force

Write-Host "打包完成，您可以直接将以下压缩包发给其他人：" -ForegroundColor Green
Write-Host ">>> $ReleaseZip" -ForegroundColor Yellow
