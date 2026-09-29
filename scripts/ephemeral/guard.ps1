<#
.SYNOPSIS
    The discipline layer: tears down overdue ephemeral leases.

.DESCRIPTION
    Intended to run on a schedule (Windows Task Scheduler / cron). If the lease
    exists, is 'up', and its max duration has passed, the stack is destroyed so
    a forgotten demo cluster cannot run away with the GBP20 budget.

    Without -AutoDestroy it only warns, so you can dry-run first.

.PARAMETER AutoDestroy
    Actually tear down an overdue stack. Without it, only warn.

.PARAMETER WarnHours
    Warn when the lease has fewer than this many hours remaining (default 2).

.PARAMETER TriggerFile
    Path to a file touched on each run (useful for heartbeat monitoring / CI).

.EXAMPLE
    .\guard.ps1 -AutoDestroy          # run every hour via Task Scheduler

    # Task Scheduler (PowerShell):
    #   powershell -NoProfile -ExecutionPolicy Bypass -File C:\...\guard.ps1 -AutoDestroy
#>
[CmdletBinding()]
param(
    [switch]$AutoDestroy,
    [double]$WarnHours = 2,
    [string]$TriggerFile = ""
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$config = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json
$leasePath = [System.IO.Path]::GetFullPath((Join-Path $here $config.leaseFile))

if ($TriggerFile) { [System.IO.File]::WriteAllText($TriggerFile, (Get-Date).ToString('o')) }

if (-not (Test-Path $leasePath)) {
    Write-Host "guard: no lease - nothing to do (stack is down)."
    return
}

$lease = Get-Content $leasePath -Raw | ConvertFrom-Json
if ($lease.state -ne 'up') {
    Write-Host "guard: stack is down - nothing to do."
    return
}

$startUtc = [datetime]::Parse($lease.createdUtc).ToUniversalTime()
$expiresUtc = $startUtc.AddHours($lease.maxLeaseHours)
$remainingHr = [math]::Round(($expiresUtc - (Get-Date).ToUniversalTime()).TotalHours, 2)

if ($remainingHr -gt $WarnHours) {
    Write-Host "guard: lease healthy - $remainingHr h remaining (cluster $($lease.clusterName))."
    return
}

if ($remainingHr -le 0) {
    Write-Host "guard: LEASE OVERDUE by $([math]::Abs($remainingHr)) h - cluster $($lease.clusterName) is costing ~GBP$($lease.estimatedCostPerHour)/hr."
    if ($AutoDestroy) {
        Write-Host "guard: AUTO-TEARDOWN..."
        & (Join-Path $here 'down.ps1') -Force
    }
    else {
        Write-Warning "guard: run .\down.ps1 -Force now, or schedule .\guard.ps1 -AutoDestroy."
    }
}
else {
    Write-Host "guard: lease expiring soon - $remainingHr h remaining."
    if ($AutoDestroy) {
        Write-Host "guard: nothing to do yet (not overdue)."
    }
}