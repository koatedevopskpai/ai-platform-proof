<#
.SYNOPSIS
    Tear the full AI Platform stack DOWN (ephemeral mode -> back to GBP0/month).

.DESCRIPTION
    - Uninstalls the Helm release (if the lease says one was installed).
    - Runs `terraform destroy` to remove ALL cloud resources.
    - Unsets the local kubectl context.
    - Records the actual uptime and estimated cost in the lease file.

.PARAMETER Force
    Skip the interactive confirmation prompt (used by guard.ps1).

.PARAMETER SkipDestroy
    Only uninstall Helm + record the lease; skip terraform destroy.

.EXAMPLE
    .\down.ps1
    .\down.ps1 -Force
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipDestroy
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$config = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json

function Resolve-RelativePath([string]$relative) {
    return [System.IO.Path]::GetFullPath((Join-Path $here $relative))
}

$tfDir     = Resolve-RelativePath $config.tfDir
$leasePath = Resolve-RelativePath $config.leaseFile

Write-Host "==> ai-platform EPHEMERAL DOWN  (region: $($config.region))"

$lease = $null
if (Test-Path $leasePath) {
    $lease = Get-Content $leasePath -Raw | ConvertFrom-Json
}

$clusterName = $lease.clusterName
$startUtc    = [datetime]::Parse($lease.createdUtc).ToUniversalTime()

# --- 1. Uninstall Helm release if one was installed ---
if ($lease -and $lease.state -eq 'up' -and $lease.helmInstalled) {
    if (Get-Command helm -ErrorAction SilentlyContinue) {
        Write-Host "==> helm uninstall ai-platform..."
        helm uninstall ai-platform --namespace ai-platform --ignore-not-found
    }
}

# --- 2. Destroy infrastructure ---
if (-not $SkipDestroy) {
    if (-not $Force) {
        $answer = Read-Host "==> This will DESTROY all AWS resources (EKS, RDS, VPC). Continue? [y/N]"
        if ($answer -notin @('y', 'Y')) {
            Write-Host "Aborted."
            return
        }
    }
    Write-Host "==> terraform destroy (this can take several minutes)..."
    Push-Location $tfDir
    try {
        terraform destroy -auto-approve
        if ($LASTEXITCODE -ne 0) { throw "terraform destroy failed" }
    }
    finally { Pop-Location }
} else {
    Write-Host "==> -SkipDestroy: leaving infrastructure in place"
}

# --- 3. Unset kubeconfig context ---
if ($clusterName) {
    aws eks update-kubeconfig --unset --name $clusterName --region $config.region 2>$null | Out-Null
}

# --- 4. Record the outcome ---
$uptimeHr = 0.0
if ($lease -and $lease.state -eq 'up') {
    $uptimeHr = [math]::Max(0.0, [math]::Round((((Get-Date).ToUniversalTime()) - $startUtc).TotalHours, 2))
}
$estCost = [math]::Round($uptimeHr * $config.rates.estimatedTotalPerHr, 2)

$result = [ordered]@{
    state             = 'down'
    tornDownUtc       = (Get-Date).ToUniversalTime().ToString('o')
    lastUptimeHours   = $uptimeHr
    estimatedCost     = $estCost
    estimatedCostPerHr = $config.rates.estimatedTotalPerHr
    clusterName       = $clusterName
}
$result | ConvertTo-Json | Set-Content $leasePath -Encoding utf8

Write-Host ""
Write-Host "==> STACK IS DOWN. Cost is back to GBP0/month until next .\up.ps1"
Write-Host ("==> Last lease: {0}h uptime, est GBP{1}" -f $uptimeHr, $estCost)