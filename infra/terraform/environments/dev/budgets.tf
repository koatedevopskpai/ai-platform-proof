# -----------------------------------------------------------------------------
# FinOps cost guardrails â€” hard monthly budget cap + auto-stop on overrun.
#
# NOTE ON REALITY: the default stack (EKS control plane + node group + RDS +
# NAT gateway) costs ~Â£200+/month. A Â£20 cap is only achievable with the
# savings profile described in docs/finops-tagging.md ("Â£20/month plan").
# The budget below is the guardrail that makes the cap ENFORCEABLE: alert at
# 80%/100% and automatically STOP RDS when the absolute cap is hit.
#
# PREREQUISITE: the `Workload` tag must be activated as a cost-allocation tag
# in AWS Billing (Cost Explorer -> Cost allocation tags) or the budget's cost
# filter will never match anything.
# -----------------------------------------------------------------------------

resource "aws_budgets_budget" "ai_platform" {
  count = var.budget_enabled ? 1 : 0

  name         = "ai-platform-${var.environment}-monthly-budget"
  budget_type  = "COST"
  limit_amount = var.budget_limit_amount
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Scope the budget to THIS workload only, via the FinOps tag.
  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Workload$ai-platform-proof"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alerts_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alerts_email]
  }
}

# --- IAM role AWS Budgets assumes to run the auto-stop action ---
resource "aws_iam_role" "budget_action" {
  count = var.budget_enabled ? 1 : 0

  name = "ai-platform-${var.environment}-budget-action"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "budgets.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

# Least-privilege: the role may ONLY stop/start THIS RDS instance.
resource "aws_iam_policy" "budget_action_stop" {
  count = var.budget_enabled ? 1 : 0

  name = "ai-platform-${var.environment}-budget-stop"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["rds:StopDBInstance", "rds:StartDBInstance"]
        Resource = module.postgres.db_instance_arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "budget_action" {
  count = var.budget_enabled ? 1 : 0

  role       = aws_iam_role.budget_action[0].name
  policy_arn = aws_iam_policy.budget_action_stop[0].arn
}

# --- Automatic guardrail: when the absolute Â£ cap is hit, STOP RDS ---
resource "aws_budgets_budget_action" "stop_rds_on_cap" {
  count = var.budget_enabled ? 1 : 0

  budget_name        = aws_budgets_budget.ai_platform[0].name
  action_type        = "RUN_SSM_DOCUMENTS"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action[0].arn

  action_threshold {
    action_threshold_type  = "ABSOLUTE_VALUE"
    action_threshold_value = var.budget_limit_amount
  }

  definition {
    ssm_action_definition {
      action_sub_type = "STOP_RDS_INSTANCES"
      instance_ids    = [module.postgres.db_instance_identifier]
      region          = var.region
    }
  }

  subscriber {
    address           = var.budget_alerts_email
    subscription_type = "EMAIL"
  }
}

# EC2 note: to also stop the EKS node groups on overrun, add a second
# `aws_budgets_budget_action` with action_type = "RUN_SSM_DOCUMENTS" and
# ssm_action_definition { action_sub_type = "STOP_EC2_INSTANCES" } targeting
# the node-group instance IDs. It is intentionally not wired here because node
# groups have min_size = 1 (a hard stop needs a scale-to-zero design), and RDS
# is the larger controllable cost. See docs/finops-tagging.md for the full
# Â£20/month plan.