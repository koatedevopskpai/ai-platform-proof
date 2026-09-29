<#
.SYNOPSIS
    Check + activate the Workload cost-allocation tag via the AWS CLI.

.DESCRIPTION
    Manual alternative to the Terraform resource
    (infra/terraform/environments/dev/cost-allocation-tags.tf).

    Cost Explorer will NOT activate a tag that is not yet applied to any
    resource. The tag appears only after you have deployed resources tagged with
    it (provider default_tags in Terraform), and AWS may take up to 24 hours to
    list it. Run WITHOUT -Activate first to check availability.

.PARAMETER TagKey
    Tag key to activate (default Workload).

.PARAMETER Activate
    Actually activate the tag (switches status to Active). Without it, only reports.

.EXAMPLE
    .\activate-cost-allocation-tags.ps1                 # check only
    .\activate-cost-allocation-tags.ps1 -Activate       # check + activate
#>
[CmdletBinding()]
param(
    [string]$TagKey = "Workload",
    [switch]$Activate
)

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { throw "aws CLI not on PATH" }

Write-Host "==> Checking cost-allocation tag availability for '$TagKey'..."
aws sts get-caller-identity | Out-Null
if ($LASTEXITCODE -ne 0) { throw "AWS credentials not configured/valid." }

$tags = (aws ce list-cost-allocation-tags --output json 2>$null | ConvertFrom-Json).CostAllocationTags
if (-not $tags) {
    Write-Host "==> Could not list cost-allocation tags (is Cost Explorer enabled?)."
    Write-Host "    Enable it in Billing -> Cost Explorer, or run 'aws ce get-...' manually."
    exit 1
}

$tag = $tags | Where-Object { $_.TagKey -eq $TagKey }
if (-not $tag) {
    Write-Host ""
    Write-Warning "Tag '$TagKey' is NOT yet available for activation."
    Write-Host "    It only appears after resources tagged '$TagKey' exist, and AWS can take up to 24h."
    Write-Host "    Next steps:"
    Write-Host "      1. Deploy infra:  scripts\cheap\up.ps1   or   scripts\ephemeral\up.ps1"
    Write-Host "      2. Wait for propagation (up to 24h), then re-run this script."
    exit 1
}

Write-Host ""
Write-Host ("    Found: {0}  Type: {1}  Status: {2}" -f $tag.TagKey, $tag.Type, $tag.Status)
if ($tag.Status -eq 'Active') {
    Write-Host "    Already ACTIVE - nothing to do. Your AWS Budget tag filter will now match."
    exit 0
}

if (-not $Activate) {
    Write-Host ""
    Write-Host "    Tag is available but INACTIVE. Re-run with -Activate to enable it:"
    Write-Host "       .\activate-cost-allocation-tags.ps1 -Activate"
    exit 0
}

Write-Host "==> Activating '$TagKey'..."
aws ce update-cost-allocation-tags-status --cost-allocation-tags-status "TagKey=$TagKey,Status=Active"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Activation failed. If the error is about the tag not being available, wait up to 24h and retry."
    exit 1
}

Write-Host ""
Write-Host "==> DONE - '$TagKey' is now Active. AWS Budgets tag filters will start matching."
Write-Host "    (Billing/cur data may take up to 24h to reflect the tag in Cost Explorer.)"