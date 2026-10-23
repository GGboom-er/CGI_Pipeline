[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('blender', 'ue')][string]$HostName,
    [ValidateSet('Preview', 'Install', 'Check', 'Uninstall')][string]$Action = 'Preview',
    [string]$Python = 'Y:/GGbommer/scripts/.conda_envs/brain/python.exe'
)
$ErrorActionPreference = 'Stop'
$Repository = (Resolve-Path "$PSScriptRoot/..").Path
if ($Action -ne 'Uninstall') {
    & $Python "$Repository/tools/check_tool_deployments.py" --deployment-host $HostName
    if ($LASTEXITCODE -ne 0) { throw 'Tool deployment check failed' }
}
$Variable = if ($HostName -eq 'blender') { 'BLENDER_SYSTEM_SCRIPTS' } else { 'UE_PYTHONPATH' }
$Directory = if ($HostName -eq 'blender') { "$PSScriptRoot/blender/scripts" } else { "$Repository/src" }
$Directory = (Resolve-Path $Directory).Path
$ReceiptPath = "$Repository/runtime/installations/$env:COMPUTERNAME-$env:USERNAME-$HostName.json"
$UserValue = [Environment]::GetEnvironmentVariable($Variable, 'User')
$MachineValue = [Environment]::GetEnvironmentVariable($Variable, 'Machine')
$Effective = if ($null -ne $UserValue) { $UserValue } else { $MachineValue }
$Parts = @($Effective -split ';' | Where-Object { $_ })
$Present = @($Parts | Where-Object { $_.TrimEnd('/','\') -ieq $Directory.TrimEnd('/','\') }).Count -gt 0
$Remaining = @($Parts | Where-Object { $_.TrimEnd('/','\') -ine $Directory.TrimEnd('/','\') })
$Receipt = if (Test-Path -LiteralPath $ReceiptPath) { Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json } else { $null }
$Desired = if ($Present) { $Effective } else { (@($Directory) + $Parts) -join ';' }
if ($Action -eq 'Uninstall') {
    if (-not $Receipt -or -not $Receipt.owned -or $Receipt.status -ne 'installed') { throw 'No active owned installation to uninstall' }
    if ($Receipt.variable -ne $Variable -or $Receipt.directory -ne $Directory) { throw 'Installation identity changed; inspect receipt first' }
    $Desired = $Remaining -join ';'
    if ($null -eq $Receipt.before -and $Desired -eq [string]$Receipt.machine) { $Desired = $null }
}
$Changed = $false
if ($Action -in @('Install','Uninstall')) {
    if ($Action -eq 'Install' -and -not $Present) {
        # Persist the original state before changing the environment; preserve other entries.
        $Receipt = [ordered]@{ variable=$Variable; directory=$Directory; before=$UserValue; machine=$MachineValue; owned=$true; status='prepared' }
        New-Item -ItemType Directory -Force -Path (Split-Path $ReceiptPath) | Out-Null
        $Receipt | ConvertTo-Json | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8
    }
    if ($UserValue -ne $Desired -and ($Action -eq 'Uninstall' -or -not $Present)) {
        [Environment]::SetEnvironmentVariable($Variable, $Desired, 'User')
        $Changed = $true
    }
    if ($Receipt -and $Receipt.owned) {
        $Receipt.status = if ($Action -eq 'Install') { 'installed' } else { 'uninstalled' }
        $Receipt | ConvertTo-Json | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8
    }
    # Existing processes keep their inherited environment. This shell is ready for new children.
    [Environment]::SetEnvironmentVariable($Variable, $Desired, 'Process')
}
$Installed = if ($Action -eq 'Install') { $true } elseif ($Action -eq 'Uninstall') { $false } else { $Present }
[ordered]@{ host=$HostName; action=$Action; variable=$Variable; directory=$Directory; installed=$Installed;
    changed=$Changed; before=$UserValue; after=$Desired; receipt=$ReceiptPath } | ConvertTo-Json
if ($Action -eq 'Check' -and -not $Present) { exit 1 }
