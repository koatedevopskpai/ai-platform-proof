<#
.SYNOPSIS
    Tear down the always-on Option A box (back to GBP 0/month).

.PARAMETER Force
    Skip the confirmation prompt.

.PARAMETER SkipDestroy
    Skip terraform destroy (leave the box running).
#>
[CmdletBinding()]
param([switch]$Force, [switch]$SkipDestroy)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$config = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json
$tfDir = [System.IO.Path]::GetFullPath((Join-Path $here $config.tfDir))

Write-Host "==> ai-platform OPTION A DOWN"

if (-not $SkipDestroy) {
    if (-not $Force) {
        $answer = Read-Host "==> This DESTROYS the EC2 box + EIP. Continue? [y/N]"
        if ($answer -notin @('y','Y')) { Write-Host "Aborted."; return }
    }
    Push-Location $tfDir
    try {
        terraform destroy -auto-approve
        if ($LASTEXITCODE -ne 0) { throw "terraform destroy failed" }
    }
    finally { Pop-Location }
} else {
    Write-Host "==> -SkipDestroy: leaving the box running"
}

Write-Host "==> OPTION A IS DOWN - cost back to GBP 0/month. Bring it back with .\up.ps1"