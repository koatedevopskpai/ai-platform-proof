variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-2"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "azs" {
  type        = list(string)
  description = "Availability zones"
  default     = ["eu-west-2a", "eu-west-2b"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.101.0/24", "10.0.102.0/24"]
}

variable "cluster_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.31"
}

variable "node_group_size" {
  description = "Desired node count"
  type        = number
  default     = 2
}

variable "node_instance_types" {
  type    = list(string)
  default = ["m5.large"]
}

variable "node_volume_size" {
  type    = number
  default = 50
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "db_username" {
  description = "Postgres username (secret value should come from tfvars / CI secret)"
  type        = string
  default     = "ai"
}

# --- FinOps tagging taxonomy ---
variable "cost_center" {
  description = "FinOps cost centre code for cost allocation"
  type        = string
  default     = "cc-ai-mlops"
}

variable "business_unit" {
  description = "Business unit owning the workload"
  type        = string
  default     = "data-platform"
}

variable "tag_owner" {
  description = "Team or individual accountable for the resource"
  type        = string
  default     = "koate.kpai@outlook.com"
}

variable "budget_owner" {
  description = "Person/team accountable for the budget of this workload"
  type        = string
  default     = "platform-leads"
}

variable "workload" {
  description = "FinOps workload identifier for cost grouping"
  type        = string
  default     = "ai-platform-proof"
}

variable "cost_category" {
  description = "Cost category used for internal chargeback/showback"
  type        = string
  default     = "engineering-rnd"
}

variable "data_classification" {
  description = "Data classification tag (public / internal / confidential)"
  type        = string
  default     = "public-reference"
}

# --- Budget & cost guardrails ---
variable "budget_enabled" {
  description = "Create the monthly AWS Budget + auto-stop guardrail"
  type        = bool
  default     = true
}

variable "budget_limit_amount" {
  description = "Hard monthly budget cap in USD (AWS Budgets only supports USD; ~GBP 20 = 26 USD)"
  type        = number
  default     = 26
}

variable "budget_alerts_email" {
  description = "Email for budget alerts (80% and 100%) and action notifications"
  type        = string
  default     = "koate.kpai@outlook.com"
}