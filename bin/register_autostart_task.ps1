[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("register", "unregister", "status")]
    [string]$Action = "register"
)

$ErrorActionPreference = "Stop"
$RegistryKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$ValueName = "CGIPipeline_MCP_AutoStart"
$ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot "manage_mcp_http.ps1")).Path
$Command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`" start"

if ($Action -eq "status") {
    $existing = (Get-ItemProperty -Path $RegistryKey -Name $ValueName -ErrorAction SilentlyContinue).$ValueName
    if ($existing) {
        [pscustomobject]@{
            Name = $ValueName
            State = "REGISTERED"
            Command = $existing
        } | ConvertTo-Json -Compress
    } else {
        [pscustomobject]@{
            Name = $ValueName
            State = "NOT_REGISTERED"
        } | ConvertTo-Json -Compress
    }
    return
}

if ($Action -eq "unregister") {
    Remove-ItemProperty -Path $RegistryKey -Name $ValueName -ErrorAction SilentlyContinue
    [pscustomobject]@{
        Name = $ValueName
        State = "UNREGISTERED"
    } | ConvertTo-Json -Compress
    return
}

Set-ItemProperty -Path $RegistryKey -Name $ValueName -Value $Command
[pscustomobject]@{
    Name = $ValueName
    State = "REGISTERED"
    Action = "REGISTERED_HKCU_RUN"
    Command = $Command
} | ConvertTo-Json -Compress
