terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  backend "s3" {
    bucket         = "ai-platform-tfstate"
    key            = "cheap/terraform.tfstate"
    region         = "eu-west-2"
    dynamodb_table = "ai-platform-tfstate-lock"
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      # --- Core / lifecycle ---
      Project     = "ai-platform"
      Environment = var.environment
      ManagedBy   = "terraform"

      # --- FinOps tagging taxonomy (cost allocation + showback) ---
      CostCenter         = var.cost_center
      BusinessUnit       = var.business_unit
      Owner              = var.tag_owner
      BudgetOwner        = var.budget_owner
      Workload           = var.workload
      CostCategory       = var.cost_category
      DataClassification = var.data_classification
    }
  }
}

# Amazon Linux 2023 (arm64) - matches the t4g/t3g Graviton instances (cheapest
# always-on option; x86_64 instances would need a different AMI + compose binary).
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-arm64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Minimal VPC: ONE public subnet, NO NAT gateway (saves ~£26/month).
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name = "ai-platform-${var.environment}-vpc"
  cidr = var.vpc_cidr

  azs            = [var.az]
  public_subnets = [var.public_subnet_cidr]

  enable_nat_gateway = false
  enable_vpn_gateway = false

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
}

resource "aws_security_group" "app" {
  name        = "ai-platform-${var.environment}-app"
  description = "AI Platform single box (Option A)"
  vpc_id      = module.vpc.vpc_id
  tags = {
    CostCenter = var.cost_center
    Workload   = var.workload
  }
}

resource "aws_security_group_rule" "app_gateway" {
  type              = "ingress"
  from_port         = 3002
  to_port           = 3002
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.app.id
}

resource "aws_security_group_rule" "app_rag" {
  type              = "ingress"
  from_port         = 8010
  to_port           = 8010
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.app.id
}

resource "aws_security_group_rule" "app_dotnet" {
  type              = "ingress"
  from_port         = 8080
  to_port           = 8080
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.app.id
}

resource "aws_security_group_rule" "app_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.app.id
}

# --- Optional SSH (used by scripts/cheap/deploy.ps1 + deploy-cheap.yml) ---
resource "aws_key_pair" "app" {
  count      = var.ssh_public_key != "" ? 1 : 0
  key_name   = "ai-platform-${var.environment}"
  public_key = var.ssh_public_key
}

resource "aws_security_group_rule" "app_ssh" {
  count             = var.ssh_public_key != "" ? 1 : 0
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = var.ssh_cidr
  security_group_id = aws_security_group.app.id
}

# --- The single box: boots, installs Docker, clones the repo, runs compose ---
resource "aws_instance" "app" {
  ami           = data.aws_ami.al2023.id
  instance_type = var.instance_type
  subnet_id     = module.vpc.public_subnets[0]

  vpc_security_group_ids = [aws_security_group.app.id]
  key_name               = var.ssh_public_key != "" ? aws_key_pair.app[0].key_name : null

  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    repo_url = var.repo_url
  })

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name       = "ai-platform-${var.environment}-app"
    CostCenter = var.cost_center
    Workload   = var.workload
  }
}

resource "aws_eip" "app" {
  instance = aws_instance.app.id
  tags = {
    CostCenter = var.cost_center
    Workload   = var.workload
  }
}