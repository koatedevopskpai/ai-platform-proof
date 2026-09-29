variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-2"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "cheap"
}

variable "az" {
  description = "Availability zone for the single subnet"
  type        = string
  default     = "eu-west-2a"
}

variable "vpc_cidr" {
  type    = string
  default = "10.1.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.1.0.0/24"
}

variable "instance_type" {
  description = "EC2 instance type (t4g.small=2GB ARM, t4g.medium=4GB recommended for on-box builds)"
  type        = string
  default     = "t4g.small"
}

variable "root_volume_size" {
  type    = number
  default = 30
}

variable "repo_url" {
  description = "Public git URL of the ai-platform-proof repo (cloned on boot and by deploy)"
  type        = string
  default     = "https://github.com/koatedevopskpai/ai-platform-proof.git"
}

variable "ssh_public_key" {
  description = "Optional SSH public key for admin/deploy access (empty = no SSH, SSM only)"
  type        = string
  default     = ""
}

variable "ssh_cidr" {
  description = "CIDR allowed to reach SSH (only used when ssh_public_key is set)"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# --- FinOps tagging taxonomy ---
variable "cost_center" {
  type    = string
  default = "cc-ai-mlops"
}

variable "business_unit" {
  type    = string
  default = "data-platform"
}

variable "tag_owner" {
  type    = string
  default = "koate.kpai@outlook.com"
}

variable "budget_owner" {
  type    = string
  default = "platform-leads"
}

variable "workload" {
  type    = string
  default = "ai-platform-proof"
}

variable "cost_category" {
  type    = string
  default = "engineering-rnd"
}

variable "data_classification" {
  type    = string
  default = "public-reference"
}

# --- Budget & cost guardrails ---
variable "budget_enabled" {
  type    = bool
  default = true
}

variable "budget_limit_amount" {
  type    = number
  default = 20
}

variable "budget_alerts_email" {
  type    = string
  default = "koate.kpai@outlook.com"
}