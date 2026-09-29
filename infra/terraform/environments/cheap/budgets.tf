# -----------------------------------------------------------------------------
# FinOps cost guardrail for the always-on Option A box.
#
# A single budget scoped to the `Workload` tag already exists in the `dev` env
# (budgets.tf). This second budget is a safety net for THIS environment: if the
# always-on box ever blows past the cap, it is automatically STOPPED.
#
# NOTE: AWS Budgets free tier = first 2 budgets; dev + cheap = exactly 2.
# PREREQUISITE: activate the `Workload` cost-allocation tag in AWS Billing.
# -----------------------------------------------------------------------------

resource "aws_budgets_budget" "cheap" {
  count = var.budget_enabled ? 1 : 0

  name         = "ai-platform-${var.environment}-monthly-budget"
  budget_type  = "COST"
  limit_amount = var.budget_limit_amount
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

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

# Least-privilege: may ONLY stop/start THIS instance.
resource "aws_iam_policy" "budget_action_stop" {
  count = var.budget_enabled ? 1 : 0

  name = "ai-platform-${var.environment}-budget-stop"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ec2:StopInstances", "ec2:StartInstances"]
        Resource = aws_instance.app.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "budget_action" {
  count = var.budget_enabled ? 1 : 0

  role       = aws_iam_role.budget_action[0].name
  policy_arn = aws_iam_policy.budget_action_stop[0].arn
}

# --- Automatic guardrail: STOP the box when the absolute cap is hit ---
resource "aws_budgets_budget_action" "stop_ec2_on_cap" {
  count = var.budget_enabled ? 1 : 0

  budget_name        = aws_budgets_budget.cheap[0].name
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
      action_sub_type = "STOP_EC2_INSTANCES"
      instance_ids    = [aws_instance.app.id]
      region          = var.region
    }
  }

  subscriber {
    address           = var.budget_alerts_email
    subscription_type = "EMAIL"
  }
}