<#
.SYNOPSIS
    Print a cost estimate for the stack as configured, and how the ephemeral
    model stays inside the GBP 20/month budget.

.DESCRIPTION
    Uses the rate table in config.json (approximate eu-west-2 on-demand list
    prices). It is an ESTIMATE for decision-making, not a bill.
#>
[CmdletBinding()]
param()

$here = $PSScriptRoot
$config = Get-Content (Join-Path $here 'config.json') -Raw | ConvertFrom-Json
$r = $config.rates

Write-Host "==> AI PLATFORM COST ESTIMATE (approx eu-west-2 on-demand list prices)"
Write-Host ""
Write-Host ("  {0,-28} {1,10}" -f 'Component', 'GBP/hr')
Write-Host ("  {0,-28} {1,10}" -f ("-" * 28), ("-" * 10))
Write-Host ("  {0,-28} {1,10}" -f 'EKS control plane', $r.eksControlPlanePerHr)
Write-Host ("  {0,-28} {1,10}" -f 'EC2 node group (2x m5.large)', $r.nodesPerHr)
Write-Host ("  {0,-28} {1,10}" -f 'RDS t4g.small', $r.rdsPerHr)
Write-Host ("  {0,-28} {1,10}" -f 'NAT gateway', $r.natGatewayPerHr)
Write-Host ("  {0,-28} {1,10}" -f 'Storage (EBS + RDS)', $r.storagePerHr)
Write-Host ("  {0,-28} {1,10}" -f ("-" * 28), ("-" * 10))
Write-Host ("  {0,-28} {1,10}" -f 'ESTIMATED TOTAL', $r.estimatedTotalPerHr)
Write-Host ""

$alwaysOnMonthly = [math]::Round(24 * 30.4 * $r.estimatedTotalPerHr, 0)
$demoDay = [math]::Round(8 * $r.estimatedTotalPerHr, 2)
$twoDays = [math]::Round(2 * $demoDay, 2)

Write-Host "==> SCENARIOS"
Write-Host ("  Always-on (24x30.4d):        ~GBP {0}/month" -f $alwaysOnMonthly)
Write-Host ("  One 8h demo day:             ~GBP {0}" -f $demoDay)
Write-Host ("  Two 8h demo days a month:    ~GBP {0}" -f $twoDays)
Write-Host ("  Fully down (ephemeral off):  GBP 0/month")
Write-Host ""
Write-Host "==> GBP 20/month budget fit"
if ($twoDays -le 20) {
    Write-Host "  Two demo days/month fit the GBP 20 budget. The AWS Budget (budgets.tf)"
    Write-Host "  is the backstop; scripts/ephemeral (up/down/guard) is the discipline."
} else {
    Write-Host "  WARNING: two demo days exceed GBP 20. Reduce demo frequency or lease length."
}
Write-Host ""
Write-Host "NOTE: exclude data-transfer, load balancers and Free-Tier credits. Rates are"
Write-Host "list prices in GBP and will drift - treat as a planning aid."