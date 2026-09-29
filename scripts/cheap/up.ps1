<#
.SYNOPSIS
    Bring the always-on Option A box UP (single EC2, ~GBP 8-15/month).

.DESCRIPTION
    Runs terraform apply for infra/terraform/environments/cheap, then prints the
    always-on URLs. Unlike Option B there is no lease/guard - this box is meant
    to stay up. The AWS Budget auto-stops it if spend ever exceeds the cap.

.PARAMETER SkipApply
    Skip terraform apply (already provisioned) and just print the URLs.
#>
[CmdletBinding()]
param([switch]$SkipApply)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$config = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json
$tfDir = [System.IO.Path]::GetFullPath((Join-Path $here $config.tfDir))

Write-Host "==> ai-platform OPTION A UP (always-on box, region $($config.region))"

if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) { throw "terraform not on PATH" }
aws sts get-caller-identity | Out-Null
if ($LASTEXITCODE -ne 0) { throw "AWS credentials not configured/valid." }

if (-not $SkipApply) {
    Write-Host "==> terraform apply (single box ~4-6 min)..."
    Push-Location $tfDir
    try {
        if (-not (Test-Path (Join-Path $tfDir '.terraform'))) { terraform init -input=false }
        terraform apply -auto-approve
        if ($LASTEXITCODE -ne 0) { throw "terraform apply failed" }
    }
    finally { Pop-Location }
}

$url = (terraform -chdir=$tfDir output -raw url).Trim()
$health = (terraform -chdir=$tfDir output -raw health_url).Trim()
$ip = (terraform -chdir=$tfDir output -raw public_ip).Trim()

Write-Host ""
Write-Host "==> ALWAYS-ON ENVIRONMENT IS UP"
Write-Host "    Gateway : $url"
Write-Host "    Health  : $health"
Write-Host ("    Est cost: ~GBP {0}/month (always-on)" -f $config.estimatedMonthly)
Write-Host ""
Write-Host "    First boot clones the repo and builds the stack (takes a few minutes)."
Write-Host "    Check readiness:  curl -s $health"
Write-Host "    Redeploy later:   .\deploy.ps1"
Write-Host "    Teardown:         .\down.ps1"