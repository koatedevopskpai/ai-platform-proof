<#
.SYNOPSIS
    Report the current state of the ephemeral AI Platform stack and its cost so far.

.DESCRIPTION
    Reads .ephemeral-lease.json. If up, prints uptime, estimated cost accrued,
    and a warning when the lease is overdue. If down, prints the GBP0/month summary.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$config = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json
$leasePath = [System.IO.Path]::GetFullPath((Join-Path $here $config.leaseFile))

if (-not (Test-Path $leasePath)) {
    Write-Host "==> No lease file. Stack is DOWN - cost GBP0/month."
    Write-Host "    Bring it up with:  .\up.ps1"
    return
}

$lease = Get-Content $leasePath -Raw | ConvertFrom-Json
$rate = $lease.estimatedCostPerHour

switch ($lease.state) {
    'up' {
        $now = Get-Date
        $startUtc = [datetime]::Parse($lease.createdUtc).ToUniversalTime()
        $elapsedHr = [math]::Round((((Get-Date).ToUniversalTime()) - $startUtc).TotalHours, 2)
        $cost = [math]::Round($elapsedHr * $rate, 2)
        $expiresUtc = $startUtc.AddHours($lease.maxLeaseHours)
        $overdue = $now.ToUniversalTime() -gt $expiresUtc

        Write-Host "==> STACK IS UP"
        Write-Host "    Cluster : $($lease.clusterName)"
        Write-Host "    Postgres: $($lease.postgresAddress)"
        Write-Host "    Helm    : $($lease.helmInstalled)"
        Write-Host ("    Uptime  : {0}h (lease max {1}h)" -f $elapsedHr, $lease.maxLeaseHours)
        Write-Host ("    Est cost: GBP{0} so far  (~GBP{1}/hr)" -f $cost, $rate)
        Write-Host ("    Expires : {0} UTC" -f $expiresUtc.ToString('yyyy-MM-dd HH:mm'))
        if ($overdue) {
            Write-Warning "LEASE IS OVERDUE - the stack is still costing money."
            Write-Host "    Teardown now:  .\down.ps1 -Force"
        }
        else {
            Write-Host ("    Teardown due:  .\down.ps1   (or guard.ps1 for auto-teardown)")
        }
    }
    'down' {
        Write-Host "==> STACK IS DOWN - cost GBP0/month until next .\up.ps1"
        Write-Host ("    Last lease: {0}h uptime, est GBP{1}" -f $lease.lastUptimeHours, $lease.estimatedCost)
    }
    default {
        Write-Warning "Unknown lease state '$($lease.state)'. Manual review of $leasePath needed."
    }
}