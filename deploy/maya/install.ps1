[CmdletBinding()]
param(
    [ValidateSet("Install", "Uninstall", "Check")]
    [string]$Action = "Install"
)

$ErrorActionPreference = "Stop"
$ModuleDirectory = (Resolve-Path (Join-Path $PSScriptRoot "modules")).Path
$VariableName = "MAYA_MODULE_PATH"

function Split-Paths([string]$Value) {
    if (-not $Value) { return @() }
    return @($Value -split [IO.Path]::PathSeparator | Where-Object { $_ })
}

function Normalize-Path([string]$Value) {
    return [IO.Path]::GetFullPath($Value).TrimEnd('\', '/')
}

function Test-ManagedPath([string]$Value) {
    try {
        return [string]::Equals(
            (Normalize-Path $Value),
            (Normalize-Path $ModuleDirectory),
            [StringComparison]::OrdinalIgnoreCase
        )
    }
    catch {
        return $false
    }
}

$UserValue = [Environment]::GetEnvironmentVariable($VariableName, "User")
$UserPaths = @(Split-Paths $UserValue)
$ManagedPaths = @($UserPaths | Where-Object { Test-ManagedPath $_ })

if ($Action -eq "Install") {
    $NewPaths = @($ModuleDirectory) + @($UserPaths | Where-Object { -not (Test-ManagedPath $_) })
    $NewValue = ($NewPaths | Select-Object -Unique) -join [IO.Path]::PathSeparator
    [Environment]::SetEnvironmentVariable($VariableName, $NewValue, "User")
    $env:MAYA_MODULE_PATH = $NewValue
}
elseif ($Action -eq "Uninstall") {
    $NewPaths = @($UserPaths | Where-Object { -not (Test-ManagedPath $_) })
    $NewValue = $NewPaths -join [IO.Path]::PathSeparator
    [Environment]::SetEnvironmentVariable(
        $VariableName,
        $(if ($NewValue) { $NewValue } else { $null }),
        "User"
    )
    $env:MAYA_MODULE_PATH = $NewValue
}
else {
    $NewValue = $UserValue
}

$Installed = @(Split-Paths $NewValue | Where-Object { Test-ManagedPath $_ }).Count -eq 1
[ordered]@{
    status = if ($Action -eq "Check") { "CHECKED" } else { "SUCCESS" }
    action = $Action
    installed = $Installed
    variable = $VariableName
    module_directory = $ModuleDirectory
    user_value = $NewValue
} | ConvertTo-Json -Compress

if ($Action -eq "Check" -and -not $Installed) { exit 1 }

