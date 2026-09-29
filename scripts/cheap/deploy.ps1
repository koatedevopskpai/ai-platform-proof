<#
.SYNOPSIS
    Redeploy the app to the Option A box over SSH (git pull + docker compose up).

.DESCRIPTION
    Requires ssh_public_key to have been set in the cheap tfvars and the private
    key at config.json -> sshKey. Pushes the repo to the box (git pull) then
    rebuilds/starts containers, then verifies the gateway /health.

.EXAMPLE
    .\deploy.ps1 -KeyPath C:\keys\ai-platform-cheap.pem
#>
[CmdletBinding()]
param(
    [string]$KeyPath = ""
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$config = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json
$tfDir = [System.IO.Path]::GetFullPath((Join-Path $here $config.tfDir))

$ip = (terraform -chdir=$tfDir output -raw public_ip).Trim()
if (-not $ip) { throw "No public IP - is the stack up? Run .\up.ps1 first." }

if (-not $KeyPath) {
    $KeyPath = $config.sshKey.Replace('~', $env:USERPROFILE)
}
if (-not (Test-Path $KeyPath)) { throw "SSH key not found at $KeyPath (set ssh_public_key in tfvars + private key in scripts/cheap/config.json)." }

Write-Host "==> Deploying to ec2-user@$ip"
ssh -i $KeyPath -o StrictHostKeyChecking=no "$($config.sshUser)@$ip" @"
set -e
cd /opt/ai-platform-proof
git pull --ff-only || true
docker compose up -d --build
sleep 10
curl -fsS http://localhost:3002/health && echo ""
echo 'DEPLOY OK'
"@

if ($LASTEXITCODE -ne 0) { throw "deploy failed (exit $LASTEXITCODE)" }
Write-Host "==> DEPLOY OK - gateway: http://${ip}:3002"