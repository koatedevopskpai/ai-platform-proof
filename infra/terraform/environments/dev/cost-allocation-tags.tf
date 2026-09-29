# -----------------------------------------------------------------------------
# Account-level cost allocation tag activation.
#
# AWS Budgets can only use a tag in its TagKeyValue cost filter once that tag is
# ACTIVATED as a cost allocation tag in Cost Explorer. This is an ACCOUNT-LEVEL
# setting, so it must be owned by exactly ONE Terraform environment (here: dev).
#
# ORDERING MATTERS: Cost Explorer only lists a tag as "available" after it has
# been applied to at least one resource. All resources inherit the `Workload`
# tag from the provider default_tags, so this resource depends on them to ensure
# they exist first.
#
# CAVEAT: AWS documents that user-defined tags can take UP TO 24 HOURS to appear
# for activation. If the first `terraform apply` fails here ("invalid parameter"),
# wait and re-run apply, or use scripts/aws/activate-cost-allocation-tags.ps1.
#
# Cost Explorer APIs are served from us-east-1, hence the alias provider.
# -----------------------------------------------------------------------------

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

resource "aws_ce_cost_allocation_tag" "workload" {
  provider = aws.us_east_1

  tag_key = "Workload"
  status  = "Active"

  depends_on = [
    module.vpc,
    module.eks,
    module.postgres,
    aws_security_group.postgres,
  ]
}