<#
.SYNOPSIS
    Show the state of the always-on Option A box and verify the gateway is live.

.DESCRIPTION
    Reads terraform outputs; if the stack exists, pings the gateway /health and
    prints estimated monthly cost. Does NOT require AWS to be perfect - it
    degrades to "no stack" when terraform outputs are unavailable.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$config = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json
$tfDir = [System.IO.Path]::GetFullPath((Join-Path $here $config.tfDir))

$url = $null
try { $url = (terraform -chdir=$tfDir output -raw url).Trim() } catch { }

if (-not $url -or $url -eq '') {
    Write-Host "==> No Option A stack found - cost GBP 0/month."
    Write-Host "    Bring it up with:  .\up.ps1"
    return
}

Write-Host "==> OPTION A IS UP (always-on)"
Write-Host "    Gateway : $url"
Write-Host ("    Est cost: ~GBP {0}/month (always-on)" -f $config.estimatedMonthly)

$ip = ($url -replace '^http://' -replace ':3002$')
$health = "http://${ip}:3002/health"
try {
    $resp = Invoke-WebRequest -Uri $health -TimeoutSec 10 -UseBasicParsing
    Write-Host "    Health  : $($resp.StatusCode) $($resp.StatusDescription)  -> $($resp.Content)"
}
catch {
    Write-Warning "    Health  : UNREACHABLE - the box may still be booting, or has been stopped by the budget."
    Write-Host "              Check AWS Console / re-run in a minute. Redeploy: .\deploy.ps1"
}