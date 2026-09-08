param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https?://')]
    [string]$BaseUrl,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [int]$TaskId
)

$ErrorActionPreference = "Stop"
$token = $env:INTRALINK_STAGING_TOKEN
if (-not $token) {
    throw "Set INTRALINK_STAGING_TOKEN for a staging operator session."
}

$root = $BaseUrl.TrimEnd('/')
$headers = @{ Authorization = "Bearer $token" }

$ready = Invoke-RestMethod -Method Get -Uri "$root/ready"
if ($ready.status -ne "ready") {
    throw "Core API is not ready: $($ready | ConvertTo-Json -Compress)"
}

$autopilot = Invoke-RestMethod -Method Get -Uri "$root/api/v2/autopilot" -Headers $headers
$run = Invoke-RestMethod -Method Get -Uri "$root/api/v2/ticket-runs/by-task/$TaskId" -Headers $headers
$metricsResponse = Invoke-WebRequest -Method Get -Uri "$root/metrics"
$relevantMetrics = @(
    $metricsResponse.Content -split "`n" |
        Where-Object {
            $_ -match '^intralink_(ticket_run|command_outbox|commands)' -and
            $_ -notmatch '^#'
        }
)

[PSCustomObject]@{
    ready = $ready.status
    autopilot_enabled = [bool]$autopilot.enabled
    templates_ready = [bool]$autopilot.templates_ready
    task_id = $TaskId
    run_id = $run.run.id
    run_mode = $run.run.mode
    run_state = $run.run.state
    pending_command_id = $run.pending_command.command_id
    pending_command_status = $run.pending_command.status
    metrics = $relevantMetrics
} | ConvertTo-Json -Depth 4
