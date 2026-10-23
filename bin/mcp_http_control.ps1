# Dot-sourced by manage_mcp_http.ps1. This file is not a service entry point.

function Read-HttpControlMetadata {
    param([object]$Identity)
    if (-not (Test-Path -LiteralPath $ControlFile -PathType Leaf)) {
        throw "Safe HTTP shutdown requires protocol-v1 control metadata; initial deployment needs an explicit coordinated cutover."
    }
    try {
        $control = Get-Content -Raw -LiteralPath $ControlFile | ConvertFrom-Json
        foreach ($field in @("protocol_version", "pid", "process_start_time", "generation", "secret")) {
            if ($control.PSObject.Properties.Name -notcontains $field) { throw "missing field" }
        }
        $generation = [guid]::Empty
        $start = [double]$control.process_start_time
        $expectedStart = ($Identity.process.StartTime.ToUniversalTime() - [datetime]::SpecifyKind([datetime]"1970-01-01", "Utc")).TotalSeconds
        if ([int]$control.protocol_version -ne 1 -or [int]$control.pid -ne $Identity.process.Id -or
            [double]::IsNaN($start) -or [double]::IsInfinity($start) -or
            [math]::Abs($start - $expectedStart) -gt 0.02 -or
            -not [guid]::TryParse([string]$control.generation, [ref]$generation) -or
            ([string]$control.secret).Length -lt 32) { throw "identity mismatch" }
    }
    catch {
        throw "HTTP control metadata is invalid or does not identify the verified service; no process was stopped."
    }
    return $control
}

function Invoke-HttpControl {
    param(
        [object]$Control,
        [ValidateSet("status", "drain", "resume", "stop")][string]$Operation,
        [long]$LeaseEpoch = 0,
        [int]$LeaseSeconds = 60
    )
    $uri = "http://${ListenAddress}:$Port/_cgi/control/$Operation"
    $headers = @{ "X-CGI-Control-Token" = [string]$Control.secret }
    $arguments = @{ Uri = $uri; Headers = $headers; TimeoutSec = 5; MaximumRedirection = 0; ErrorAction = "Stop" }
    if ($Operation -eq "status") {
        $arguments.Uri += "?generation=$([uri]::EscapeDataString([string]$Control.generation))"
        $arguments.Method = "Get"
    }
    else {
        $body = @{ generation = [string]$Control.generation; lease_epoch = $LeaseEpoch }
        if ($Operation -eq "drain") { $body.lease_seconds = $LeaseSeconds }
        $arguments.Method = "Post"
        $arguments.ContentType = "application/json"
        $arguments.Body = $body | ConvertTo-Json -Compress
    }
    try { $response = Invoke-RestMethod @arguments }
    catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { "unavailable" }
        throw "HTTP control $Operation request failed ($code); control credentials were not disclosed."
    }
    if (-not $response -or [int]$response.protocol_version -ne 1 -or
        [int]$response.pid -ne [int]$Control.pid -or
        [string]$response.generation -cne [string]$Control.generation -or
        [double]::IsNaN([double]$response.process_start_time) -or
        [double]::IsInfinity([double]$response.process_start_time) -or
        [math]::Abs([double]$response.process_start_time - [double]$Control.process_start_time) -gt 0.02 -or
        $response.PSObject.Properties.Name -contains "secret") {
        throw "HTTP control response identity mismatch; no process was stopped."
    }
    return $response
}

function Stop-HttpServiceCooperatively {
    param([object]$Identity)
    $control = Read-HttpControlMetadata $Identity
    $leaseEpoch = 0L
    $leaseAcquired = $false
    $stopCommitted = $false
    try {
        $status = Invoke-HttpControl $control "status"
        if ($status.state -eq "stopping") { throw "HTTP service already committed to shutdown; no forced termination was attempted." }
        $leaseEpoch = [long]$status.lease_epoch
        $timer = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            if ($timer.Elapsed.TotalSeconds -ge $DrainTimeoutSeconds) {
                throw "HTTP drain timed out after ${DrainTimeoutSeconds}s; existing service was preserved."
            }
            $status = Invoke-HttpControl $control "drain" $leaseEpoch 60
            $leaseEpoch = [long]$status.lease_epoch
            $leaseAcquired = $true
            if ($status.state -ne "draining") { throw "HTTP service did not enter the requested drain state." }
            if ([long]$status.active_count -eq 0) { break }
            Start-Sleep -Milliseconds 250
        }
        # Preserve the shared Worker before committing shutdown. This helper is
        # bounded to 20 seconds, inside the freshly renewed 60-second lease.
        $cleanupResult = Invoke-CooperativeShutdown
        if ($cleanupResult.status -ne "COMPLETED") {
            throw "Shared Worker preservation failed ($($cleanupResult.status)); existing HTTP service was preserved."
        }
        $confirmed = Get-ManagedIdentity -RequireListener -AllowRepositoryRelocation
        if (-not $confirmed.valid -or $confirmed.process.Id -ne $Identity.process.Id -or
            $confirmed.process.StartTime.ToUniversalTime().Ticks -ne $Identity.process.StartTime.ToUniversalTime().Ticks) {
            throw "Managed process identity changed before shutdown commit; no process was stopped."
        }
        $stopped = Invoke-HttpControl $control "stop" $leaseEpoch
        if ($stopped.state -ne "stopping" -or [long]$stopped.active_count -ne 0) {
            throw "HTTP service did not confirm an idle atomic shutdown commit."
        }
        $stopCommitted = $true
        if (-not $Identity.process.WaitForExit($ShutdownTimeoutSeconds * 1000)) {
            throw "HTTP stop committed but PID $($Identity.process.Id) did not exit within ${ShutdownTimeoutSeconds}s; no forced termination was attempted."
        }
        return $cleanupResult
    }
    catch {
        $originalError = $_
        if ($leaseAcquired -and -not $stopCommitted) {
            try { [void](Invoke-HttpControl $control "resume" $leaseEpoch) }
            catch { Write-Warning 'Resume was not confirmed; inspect frontend state before retrying.' }
        }
        throw $originalError
    }
}

function Stop-HttpServiceForInitialProtocolCutover {
    param([object]$Identity)
    if (Test-Path -LiteralPath $ControlFile) {
        throw "Initial protocol cutover is forbidden when control metadata exists; use the authenticated shutdown protocol."
    }
    $receiptPath = [string]$InitialProtocolCutoverReceipt
    $projectsRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "projects")).TrimEnd('\', '/')
    if (-not [IO.Path]::IsPathFullyQualified($receiptPath) -or
        -not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        throw "Initial protocol cutover requires an existing absolute coordination receipt."
    }
    $receiptPath = (Resolve-Path -LiteralPath $receiptPath).Path
    if (-not $receiptPath.StartsWith($projectsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Initial protocol cutover receipt must be inside a managed projects workspace."
    }
    $entry = Get-Item -LiteralPath $receiptPath -Force
    while ($entry) {
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Initial protocol cutover receipt must not traverse reparse points."
        }
        if ([string]::Equals($entry.FullName.TrimEnd('\', '/'), $projectsRoot, [StringComparison]::OrdinalIgnoreCase)) { break }
        $entry = if ($entry -is [IO.FileInfo]) { $entry.Directory } else { $entry.Parent }
    }
    Confirm-InitialCutoverWorkspace $receiptPath $projectsRoot
    try {
        $receipt = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
        $approvedTicks = ConvertTo-UtcTicks $receipt.approved_at_utc
        $expiresTicks = ConvertTo-UtcTicks $receipt.expires_at_utc
        if ($null -eq $approvedTicks -or $null -eq $expiresTicks) { throw "invalid approval timestamps" }
        $approvedAt = [datetime]::new($approvedTicks, [DateTimeKind]::Utc)
        $expiresAt = [datetime]::new($expiresTicks, [DateTimeKind]::Utc)
        $now = [DateTime]::UtcNow
        if ($receipt.schema_version -ne 1 -or $receipt.kind -cne "cgi_http_initial_protocol_cutover" -or
            $receipt.approved -isnot [bool] -or -not $receipt.approved -or
            [int]$receipt.port -ne $Port -or [int]$receipt.pid -ne $Identity.process.Id -or
            (ConvertTo-UtcTicks $receipt.creation_time) -ne $Identity.process.StartTime.ToUniversalTime().Ticks -or
            -not ([string]$receipt.server_path).Trim() -or
            -not [string]::Equals([string]$receipt.server_path, [string]$Identity.managed_server_path, [StringComparison]::OrdinalIgnoreCase) -or
            $approvedAt -gt $now -or $expiresAt -le $now -or $expiresAt -le $approvedAt -or
            ($expiresAt - $approvedAt).TotalSeconds -gt 900) { throw "invalid identity or approval" }
        $participants = @($receipt.coordination)
        if ($participants.Count -lt 2) { throw "coordination acknowledgements missing" }
        $ids = @{}
        foreach ($participant in $participants) {
            if ($participant.ack -isnot [bool] -or -not $participant.ack -or
                -not ([string]$participant.thread_id).Trim() -or -not ([string]$participant.title).Trim() -or
                -not ([string]$participant.evidence).Trim() -or $ids.ContainsKey([string]$participant.thread_id)) {
                throw "invalid coordination acknowledgement"
            }
            $ids[[string]$participant.thread_id] = $true
        }
    }
    catch { throw "Initial protocol cutover receipt is invalid, expired, or does not match the verified service." }
    $cleanupResult = Invoke-CooperativeShutdown
    if ($cleanupResult.status -ne "COMPLETED") {
        throw "Shared Worker preservation failed; initial protocol cutover was not performed."
    }
    $confirmed = Get-ManagedIdentity -RequireListener -AllowRepositoryRelocation
    if (-not $confirmed.valid -and $Identity.legacy_module_entry) { $confirmed = Get-LegacyModuleIdentity }
    if (-not $confirmed.valid -or $confirmed.process.Id -ne $Identity.process.Id -or
        $confirmed.process.StartTime.ToUniversalTime().Ticks -ne $Identity.process.StartTime.ToUniversalTime().Ticks -or
        (Test-Path -LiteralPath $ControlFile)) {
        throw "HTTP identity or protocol changed before initial cutover; no process was stopped."
    }
    # Consume before terminating. A crash cannot make the same approval reusable.
    [IO.File]::Move($receiptPath, "$receiptPath.consumed")
    if (-not $Identity.process.HasExited) {
        Stop-Process -InputObject $Identity.process -Force
        if (-not $Identity.process.WaitForExit($ShutdownTimeoutSeconds * 1000)) {
            throw "Approved initial protocol cutover did not exit; receipt is consumed and no further termination was attempted."
        }
    }
    return [pscustomobject]@{ status = "COMPLETED"; detail = "Explicit coordinated initial protocol cutover; shared Worker preserved." }
}

function Confirm-InitialCutoverWorkspace {
    param([string]$ReceiptPath, [string]$ProjectsRoot)
    $parts = [IO.Path]::GetRelativePath($ProjectsRoot, $ReceiptPath) -split '[/\\]'
    if ($parts.Count -lt 3) { throw "Initial protocol cutover receipt has no managed workspace ancestor." }
    $workspaceRoot = Join-Path (Join-Path $ProjectsRoot $parts[0]) $parts[1]
    $python = Resolve-PythonPath
    # Reuse the same identity owner as run allocation/recovery. This is read-only;
    # do not duplicate run_state fields here or create/adopt a workspace.
    $code = "import sys; from cgi_pipeline.core.workspace import _validate_run_identity; _validate_run_identity(sys.argv[1], sys.argv[3], sys.argv[2])"
    try {
        & $python -B -s -c $code $workspaceRoot $parts[0] $parts[1] 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "native identity rejected" }
    }
    catch { throw "Initial protocol cutover receipt is not inside a valid native CGI workspace." }
}
