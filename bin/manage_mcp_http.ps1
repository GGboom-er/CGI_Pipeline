[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "status", "stop", "restart")]
    [string]$Action = "status",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$PythonPath = "",
    [ValidateRange(1, 120)]
    [int]$StartupTimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"
$ListenAddress = "127.0.0.1"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$NotesPythonPrefix = (Resolve-Path (Join-Path (Split-Path $ProjectRoot -Parent) "conda_envs\cgi_pipeline") -ErrorAction SilentlyContinue).Path
if ($NotesPythonPrefix) {
    $env:PYTHONNOUSERSITE = "1"
    $managedPath = @(
        $NotesPythonPrefix,
        (Join-Path $NotesPythonPrefix "Library\mingw-w64\bin"),
        (Join-Path $NotesPythonPrefix "Library\usr\bin"),
        (Join-Path $NotesPythonPrefix "Library\bin"),
        (Join-Path $NotesPythonPrefix "Scripts")
    ) | Where-Object { Test-Path -LiteralPath $_ }
    $env:PATH = (($managedPath + ($env:PATH -split [IO.Path]::PathSeparator)) |
        Where-Object { $_ } | Select-Object -Unique) -join [IO.Path]::PathSeparator
}
$ServerPath = (Resolve-Path (Join-Path $ProjectRoot "mcp_server\server.py")).Path
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$LogDir = Join-Path $ProjectRoot "logs"
$PidFile = Join-Path $RuntimeDir "mcp_http_$Port.pid"
$StdoutLog = Join-Path $LogDir "mcp_http_$Port.stdout.log"
$StderrLog = Join-Path $LogDir "mcp_http_$Port.stderr.log"
$Endpoint = "http://${ListenAddress}:$Port/mcp"
$MutexName = "Global\CGIPipeline.McpHttp.Port$Port"

function Write-ServiceResult {
    param([object]$Value)
    $Value | ConvertTo-Json -Depth 4 -Compress
}

function Get-ListenerPid {
    $endpoint = "${ListenAddress}:$Port"
    foreach ($line in (& netstat.exe -ano -p tcp)) {
        $parts = $line.Trim() -split '\s+'
        if ($parts.Count -ge 5 -and
            $parts[0] -eq "TCP" -and
            $parts[1] -eq $endpoint -and
            $parts[3] -eq "LISTENING") {
            return [int]$parts[4]
        }
    }
    return $null
}

function Get-ProcessRecord {
    param([int]$ProcessId)
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }
    try {
        $creationTime = $process.StartTime.ToUniversalTime()
    }
    catch {
        return $null
    }
    $commandLine = $null
    if ($process.PSObject.Properties.Name -contains "CommandLine") {
        $commandLine = $process.CommandLine
    }
    if (-not $commandLine) {
        $cimRecord = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
        if ($cimRecord) {
            $commandLine = $cimRecord.CommandLine
        }
    }
    return [pscustomobject]@{
        process = $process
        creation_time = $creationTime
        command_line = $commandLine
    }
}

function Test-ServerProcess {
    param([int]$ProcessId)
    $record = Get-ProcessRecord $ProcessId
    if (-not $record -or -not $record.command_line) {
        return $false
    }
    $command = $record.command_line.Replace("\", "/").ToLowerInvariant()
    $server = $ServerPath.Replace("\", "/").ToLowerInvariant()
    return $command.Contains($server) -and $command -match '(^|\s)--http(\s|$)'
}

function Read-PidMetadata {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        return [pscustomobject]@{
            exists = $false
            valid_json = $false
            metadata = $null
            reason = "no_pid_file"
        }
    }
    try {
        $metadata = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
    }
    catch {
        return [pscustomobject]@{
            exists = $true
            valid_json = $false
            metadata = $null
            reason = "invalid_pid_metadata_json"
        }
    }
    $requiredFields = @("pid", "creation_time", "port", "server_path")
    foreach ($field in $requiredFields) {
        if (-not $metadata -or $metadata.PSObject.Properties.Name -notcontains $field) {
            return [pscustomobject]@{
                exists = $true
                valid_json = $false
                metadata = $metadata
                reason = "missing_pid_metadata_$field"
            }
        }
    }
    return [pscustomobject]@{
        exists = $true
        valid_json = $true
        metadata = $metadata
        reason = "metadata_loaded"
    }
}

function ConvertTo-UtcTicks {
    param([object]$Value)
    try {
        if ($Value -is [datetime]) {
            return ([datetime]$Value).ToUniversalTime().Ticks
        }
        $parsed = [DateTimeOffset]::Parse(
            [string]$Value,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        )
        return $parsed.UtcDateTime.Ticks
    }
    catch {
        return $null
    }
}

function Get-ManagedIdentity {
    param([switch]$RequireListener)
    $loaded = Read-PidMetadata
    if (-not $loaded.valid_json) {
        return [pscustomobject]@{
            exists = $loaded.exists
            valid = $false
            reason = $loaded.reason
            metadata = $loaded.metadata
            process = $null
        }
    }
    $metadata = $loaded.metadata
    $metadataPid = 0
    $metadataPort = 0
    if (-not [int]::TryParse([string]$metadata.pid, [ref]$metadataPid) -or $metadataPid -le 0) {
        return [pscustomobject]@{ exists = $true; valid = $false; reason = "invalid_metadata_pid"; metadata = $metadata; process = $null }
    }
    if (-not [int]::TryParse([string]$metadata.port, [ref]$metadataPort) -or $metadataPort -ne $Port) {
        return [pscustomobject]@{ exists = $true; valid = $false; reason = "metadata_port_mismatch"; metadata = $metadata; process = $null }
    }
    if (-not [string]::Equals([string]$metadata.server_path, $ServerPath, [StringComparison]::OrdinalIgnoreCase)) {
        return [pscustomobject]@{ exists = $true; valid = $false; reason = "metadata_server_path_mismatch"; metadata = $metadata; process = $null }
    }
    $record = Get-ProcessRecord $metadataPid
    if (-not $record) {
        return [pscustomobject]@{ exists = $true; valid = $false; reason = "metadata_process_missing"; metadata = $metadata; process = $null }
    }
    $expectedTicks = ConvertTo-UtcTicks $metadata.creation_time
    if ($null -eq $expectedTicks -or $expectedTicks -ne $record.creation_time.Ticks) {
        return [pscustomobject]@{ exists = $true; valid = $false; reason = "metadata_creation_time_mismatch"; metadata = $metadata; process = $record.process }
    }
    if (-not $record.command_line) {
        return [pscustomobject]@{ exists = $true; valid = $false; reason = "metadata_command_line_unavailable"; metadata = $metadata; process = $record.process }
    }
    $command = $record.command_line.Replace("\", "/").ToLowerInvariant()
    $server = $ServerPath.Replace("\", "/").ToLowerInvariant()
    if (-not $command.Contains($server) -or $command -notmatch '(^|\s)--http(\s|$)') {
        return [pscustomobject]@{ exists = $true; valid = $false; reason = "metadata_command_line_mismatch"; metadata = $metadata; process = $record.process }
    }
    if ($RequireListener) {
        $listenerPid = Get-ListenerPid
        if ($null -eq $listenerPid) {
            return [pscustomobject]@{ exists = $true; valid = $false; reason = "metadata_process_not_listening"; metadata = $metadata; process = $record.process }
        }
        if ($listenerPid -ne $metadataPid) {
            return [pscustomobject]@{ exists = $true; valid = $false; reason = "metadata_listener_pid_mismatch"; metadata = $metadata; process = $record.process }
        }
    }
    return [pscustomobject]@{
        exists = $true
        valid = $true
        reason = "metadata_identity_match"
        metadata = $metadata
        process = $record.process
    }
}

function Write-PidMetadata {
    param([System.Diagnostics.Process]$Process)
    $metadata = [ordered]@{
        pid = $Process.Id
        creation_time = $Process.StartTime.ToUniversalTime().ToString("o", [Globalization.CultureInfo]::InvariantCulture)
        port = $Port
        server_path = $ServerPath
    }
    $json = $metadata | ConvertTo-Json -Compress
    $temporaryPath = "$PidFile.$([guid]::NewGuid().ToString('N')).tmp"
    $utf8WithoutBom = [Text.UTF8Encoding]::new($false)
    try {
        [IO.File]::WriteAllText($temporaryPath, $json, $utf8WithoutBom)
        if ([IO.File]::Exists($PidFile)) {
            try {
                [IO.File]::Replace($temporaryPath, $PidFile, $null)
            }
            catch {
                Move-Item -LiteralPath $temporaryPath -Destination $PidFile -Force
            }
        }
        else {
            [IO.File]::Move($temporaryPath, $PidFile)
        }
    }
    finally {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
    }
}

function Remove-PidMetadata {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Invoke-WithPortLock {
    param([scriptblock]$ScriptBlock)
    $mutex = [Threading.Mutex]::new($false, $MutexName)
    $acquired = $false
    try {
        try {
            $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds($StartupTimeoutSeconds + 10))
        }
        catch [Threading.AbandonedMutexException] {
            $acquired = $true
        }
        if (-not $acquired) {
            throw "Timed out waiting for the CGI Pipeline HTTP manager lock for port $Port"
        }
        return (& $ScriptBlock)
    }
    finally {
        if ($acquired) {
            [void]$mutex.ReleaseMutex()
        }
        $mutex.Dispose()
    }
}

function Get-ServiceState {
    $listenerPid = Get-ListenerPid
    $identity = Get-ManagedIdentity -RequireListener
    $running = $null -ne $listenerPid
    $serverMatch = $running -and (Test-ServerProcess $listenerPid)
    return [pscustomobject]@{
        status = if ($running) { "RUNNING" } else { "STOPPED" }
        managed = $running -and $identity.valid
        server_match = $serverMatch
        metadata_reason = $identity.reason
        pid = $listenerPid
        host = $ListenAddress
        port = $Port
        endpoint = $Endpoint
        pid_file = $PidFile
        stdout_log = $StdoutLog
        stderr_log = $StderrLog
    }
}

function Resolve-PythonPath {
    function Test-Python311 {
        param([string]$Candidate)
        try {
            $version = (& $Candidate -s -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null).Trim()
            return $version -eq "3.11"
        }
        catch {
            return $false
        }
    }

    $canonical = Join-Path (Split-Path $ProjectRoot -Parent) "conda_envs\cgi_pipeline\python.exe"
    if (-not (Test-Path -LiteralPath $canonical -PathType Leaf)) {
        throw "Canonical CGI Python not found: $canonical. Run bin\setup.bat first."
    }
    $canonical = (Resolve-Path -LiteralPath $canonical).Path
    if ($PythonPath) {
        $requested = (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path
        if ($requested -ne $canonical) {
            throw "CGI Pipeline uses one canonical Python only: $canonical"
        }
    }
    if (-not (Test-Python311 $canonical)) {
        throw "Canonical CGI Python must be 3.11: $canonical"
    }
    return $canonical
}

function Invoke-CooperativeShutdown {
    $cleanupProcess = [Diagnostics.Process]::new()
    try {
        $python = Resolve-PythonPath
        $cleanupCode = "from core.service_manager import shutdown_all; shutdown_all()"
        $startInfo = [Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $python
        $startInfo.Arguments = "-s -c `"$cleanupCode`""
        $startInfo.WorkingDirectory = $ProjectRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $cleanupProcess.StartInfo = $startInfo
        if (-not $cleanupProcess.Start()) {
            return [pscustomobject]@{
                status = "FAILED"
                detail = "Could not start shutdown_all helper"
            }
        }
        $stdoutTask = $cleanupProcess.StandardOutput.ReadToEndAsync()
        $stderrTask = $cleanupProcess.StandardError.ReadToEndAsync()
        if (-not $cleanupProcess.WaitForExit(20000)) {
            $cleanupProcess.Kill()
            $cleanupProcess.WaitForExit()
            [void]$stdoutTask.GetAwaiter().GetResult()
            [void]$stderrTask.GetAwaiter().GetResult()
            return [pscustomobject]@{
                status = "TIMEOUT"
                detail = "shutdown_all did not finish within 20 seconds"
            }
        }
        [void]$stdoutTask.GetAwaiter().GetResult()
        $cleanupError = $stderrTask.GetAwaiter().GetResult().Trim()
        if ($cleanupProcess.ExitCode -ne 0) {
            return [pscustomobject]@{
                status = "FAILED"
                detail = if ($cleanupError) { $cleanupError } else { "shutdown_all exited with code $($cleanupProcess.ExitCode)" }
            }
        }
        return [pscustomobject]@{
            status = "COMPLETED"
            detail = "shutdown_all completed before HTTP process termination"
        }
    }
    catch {
        return [pscustomobject]@{
            status = "FAILED"
            detail = $_.Exception.Message
        }
    }
    finally {
        $cleanupProcess.Dispose()
    }
}

function Stop-ManagedService {
    $identity = Get-ManagedIdentity -RequireListener
    if (-not $identity.valid) {
        if ($identity.exists) {
            Remove-PidMetadata
        }
        $listenerPid = Get-ListenerPid
        if ($null -ne $listenerPid) {
            throw "Refusing to stop unmanaged listener PID $listenerPid on $Endpoint ($($identity.reason))"
        }
        return [pscustomobject]@{
            status = "STOPPED"
            managed = $false
            pid = $null
            endpoint = $Endpoint
            message = if ($identity.exists) { "Stale CGI Pipeline HTTP metadata removed; no process was stopped." } else { "No managed CGI Pipeline HTTP service is running." }
        }
    }

    # Re-read every identity component immediately before termination. The Process
    # object then keeps the verified OS process handle, avoiding a PID-only kill.
    $confirmed = Get-ManagedIdentity -RequireListener
    if (-not $confirmed.valid) {
        throw "Managed process identity changed before stop ($($confirmed.reason)); no process was stopped."
    }
    $managedProcess = $confirmed.process
    $managedProcessId = $managedProcess.Id
    $cleanupResult = Invoke-CooperativeShutdown
    if (-not $managedProcess.HasExited) {
        Stop-Process -InputObject $managedProcess -Force
        if (-not $managedProcess.WaitForExit(5000)) {
            throw "Managed service PID $managedProcessId did not exit after forced termination"
        }
    }
    Remove-PidMetadata
    $listenerPid = Get-ListenerPid
    if ($null -ne $listenerPid) {
        throw "A listener PID $listenerPid is still present on $Endpoint after stopping managed PID $managedProcessId"
    }
    return [pscustomobject]@{
        status = "STOPPED"
        managed = $true
        pid = $managedProcessId
        endpoint = $Endpoint
        shutdown_cleanup = $cleanupResult.status
        shutdown_detail = $cleanupResult.detail
        message = "Managed CGI Pipeline HTTP service stopped."
    }
}

function Start-ManagedService {
    $state = Get-ServiceState
    if ($state.status -eq "RUNNING") {
        if (-not $state.server_match) {
            throw "Port $Port is occupied by unrelated PID $($state.pid)"
        }
        $state.status = "ALREADY_RUNNING"
        return $state
    }

    $oldIdentity = Get-ManagedIdentity
    if ($oldIdentity.exists) {
        if ($oldIdentity.valid) {
            throw "Managed CGI Pipeline HTTP PID $($oldIdentity.metadata.pid) exists but is not listening on $Endpoint; refusing a duplicate start."
        }
        Remove-PidMetadata
    }

    New-Item -ItemType Directory -Force -Path $RuntimeDir, $LogDir | Out-Null
    $python = Resolve-PythonPath
    $oldHost = [Environment]::GetEnvironmentVariable("MCP_HOST", "Process")
    $oldPort = [Environment]::GetEnvironmentVariable("MCP_PORT", "Process")
    try {
        $env:MCP_HOST = $ListenAddress
        $env:MCP_PORT = [string]$Port
        $quotedServerPath = '"' + $ServerPath + '"'
        $process = Start-Process -FilePath $python `
            -ArgumentList @("-s", $quotedServerPath, "--http") `
            -WorkingDirectory $ProjectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $StdoutLog `
            -RedirectStandardError $StderrLog `
            -PassThru
    }
    finally {
        [Environment]::SetEnvironmentVariable("MCP_HOST", $oldHost, "Process")
        [Environment]::SetEnvironmentVariable("MCP_PORT", $oldPort, "Process")
    }

    Write-PidMetadata $process
    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $process.Refresh()
        if ($process.HasExited) {
            Remove-PidMetadata
            throw "CGI Pipeline HTTP service exited with code $($process.ExitCode). See $StderrLog"
        }
        $listenerPid = Get-ListenerPid
        if ($null -ne $listenerPid) {
            if ($listenerPid -ne $process.Id) {
                if (-not $process.HasExited) {
                    Stop-Process -InputObject $process -Force -ErrorAction SilentlyContinue
                }
                Remove-PidMetadata
                throw "Port $Port was claimed by PID $listenerPid while starting PID $($process.Id)"
            }
            return (Get-ServiceState)
        }
        Start-Sleep -Milliseconds 100
    }

    if (-not $process.HasExited) {
        Stop-Process -InputObject $process -Force -ErrorAction SilentlyContinue
    }
    Remove-PidMetadata
    throw "CGI Pipeline HTTP service did not listen on $Endpoint within ${StartupTimeoutSeconds}s. See $StderrLog"
}

try {
    $result = switch ($Action) {
        "status" { Get-ServiceState }
        "start" { Invoke-WithPortLock { Start-ManagedService } }
        "stop" { Invoke-WithPortLock { Stop-ManagedService } }
        "restart" {
            Invoke-WithPortLock {
                [void](Stop-ManagedService)
                Start-ManagedService
            }
        }
    }
    Write-ServiceResult $result
}
catch {
    Write-ServiceResult ([pscustomobject]@{
        status = "ERROR"
        managed = $false
        endpoint = $Endpoint
        error = $_.Exception.Message
        pid_file = $PidFile
        stdout_log = $StdoutLog
        stderr_log = $StderrLog
    })
    exit 1
}
