<#
.SYNOPSIS
    Bring the full AI Platform stack UP on AWS for a short, cost-controlled demo
    (the "local-first, ephemeral" FinOps mode).

.DESCRIPTION
    - Provisions infrastructure with Terraform (EKS + RDS + VPC).
    - Configures kubectl for the cluster.
    - Optionally deploys the Helm chart (requires images pushed to ECR first).
    - Writes a lease file (.ephemeral-lease.json) that records the start time,
      max lease duration and estimated cost rate.

    The AWS Budget in budgets.tf (hard GBP20/month) remains the backstop; the lease
    is the discipline layer so a forgotten cluster gets torn down.

.PARAMETER DeployHelm
    Also run `helm upgrade --install` after the cluster is ready.

.PARAMETER Registry
    Image registry for Helm (e.g. ECR registry URL). Required when -DeployHelm.

.PARAMETER ImageTag
    Image tag for Helm (defaults to the value in helm values.yaml).

.PARAMETER SkipApply
    Skip `terraform apply` (for when infra already exists and you only re-deploy).

.EXAMPLE
    .\up.ps1
    .\up.ps1 -DeployHelm -Registry 123456789012.dkr.ecr.eu-west-2.amazonaws.com -ImageTag latest
#>
[CmdletBinding()]
param(
    [switch]$DeployHelm,
    [string]$Registry,
    [string]$ImageTag = "",
    [switch]$SkipApply
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$config = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json

function Resolve-RelativePath([string]$relative) {
    return [System.IO.Path]::GetFullPath((Join-Path $here $relative))
}

$tfDir      = Resolve-RelativePath $config.tfDir
$helmDir    = Resolve-RelativePath $config.helmChartDir
$leasePath  = Resolve-RelativePath $config.leaseFile

Write-Host "==> ai-platform EPHEMERAL UP  (region: $($config.region))"

# --- 1. Pre-flight checks ---
foreach ($tool in 'terraform', 'aws', 'kubectl') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "Missing required tool: $tool"
    }
}
if ($DeployHelm -and -not (Get-Command helm -ErrorAction SilentlyContinue)) {
    throw "helm is required when -DeployHelm is used"
}
Write-Host "==> Checking AWS credentials..."
aws sts get-caller-identity | Out-Null
if ($LASTEXITCODE -ne 0) { throw "AWS credentials not configured/valid. Run 'aws configure' or set up SSO." }

# --- 2. Provision infrastructure ---
if (-not $SkipApply) {
    Write-Host "==> Terraform apply (this can take 10-20 minutes)..."
    Push-Location $tfDir
    try {
        if (-not (Test-Path (Join-Path $tfDir '.terraform'))) {
            terraform init -input=false
        }
        terraform apply -auto-approve
        if ($LASTEXITCODE -ne 0) { throw "terraform apply failed" }
    }
    finally { Pop-Location }
} else {
    Write-Host "==> -SkipApply: using existing infrastructure"
}

# --- 3. Configure kubectl + collect outputs ---
$clusterName = (terraform -chdir=$tfDir output -raw cluster_name).Trim()
$pgAddr      = (terraform -chdir=$tfDir output -raw postgres_address).Trim()
Write-Host "==> Cluster: $clusterName  |  Postgres: $pgAddr"
aws eks update-kubeconfig --name $clusterName --region $config.region | Out-Null
if ($LASTEXITCODE -ne 0) { throw "aws eks update-kubeconfig failed" }

# --- 4. Optional Helm deploy ---
$helmInstalled = $false
if ($DeployHelm) {
    if (-not $Registry) { throw "-Registry is required when -DeployHelm" }
    Write-Host "==> Helm install..."
    $set = "--set global.imageRegistry=$Registry"
    if ($ImageTag) { $set += " --set global.imageTag=$ImageTag" }
    helm upgrade --install ai-platform $helmDir --namespace ai-platform --create-namespace --wait $set
    if ($LASTEXITCODE -ne 0) { throw "helm install failed" }
    $helmInstalled = $true
}

# --- 5. Write the lease ---
$lease = [ordered]@{
    state                = 'up'
    createdUtc           = (Get-Date).ToUniversalTime().ToString('o')
    maxLeaseHours        = $config.maxLeaseHours
    clusterName          = $clusterName
    postgresAddress      = $pgAddr
    helmInstalled        = $helmInstalled
    estimatedCostPerHour = $config.rates.estimatedTotalPerHr
}
$lease | ConvertTo-Json | Set-Content $leasePath -Encoding utf8

$maxCost = [math]::Round($config.maxLeaseHours * $config.rates.estimatedTotalPerHr, 2)
Write-Host ""
Write-Host "==> STACK IS UP. Lease file: $leasePath"
Write-Host ("==> Estimated cost: ~GBP{0}/hr  |  max ~GBP{1} for a {2}h lease" -f $config.rates.estimatedTotalPerHr, $maxCost, $config.maxLeaseHours)
Write-Host ("==> Lease expires: {0} UTC" -f (Get-Date).ToUniversalTime().AddHours($config.maxLeaseHours).ToString('yyyy-MM-dd HH:mm'))
Write-Host ""
Write-Host "==> Remember to tear down when done:  .\down.ps1"
Write-Host "==> Schedule the guard to auto-teardown overdue leases:  .\guard.ps1 -AutoDestroy"