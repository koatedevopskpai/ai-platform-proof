terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  backend "s3" {
    # Configure per environment: bucket, key, region, dynamodb_table for locking.
    bucket         = "ai-platform-proof-tfstate"
    key            = "dev/terraform.tfstate"
    region         = "eu-west-2"
    use_lockfile   = true
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

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name = "ai-platform-${var.environment}-vpc"
  cidr = var.vpc_cidr

  azs             = var.azs
  private_subnets = var.private_subnet_cidrs
  public_subnets  = var.public_subnet_cidrs

  enable_nat_gateway = true
  single_nat_gateway = true

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name    = "ai-platform-${var.environment}"
  cluster_version = var.cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access = false

  eks_managed_node_groups = {
    main = {
      desired_size = var.node_group_size
      min_size     = 1
      max_size     = var.node_group_size + 2

      instance_types = var.node_instance_types

      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = var.node_volume_size
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }
    }
  }

  tags = {
    Environment = var.environment
    # FinOps tags are inherited from provider default_tags; these augment for
    # node-group-level cost granularity.
    CostCenter  = var.cost_center
    BudgetOwner = var.budget_owner
    Workload    = var.workload
  }
}

module "postgres" {
  source  = "terraform-aws-modules/rds/aws"
  version = "~> 6.10"

  identifier = "ai-platform-${var.environment}-pgvector"

  engine         = "postgres"
  engine_version = "16.4"
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_encrypted     = true

  db_name  = "aiplatform"
  username = var.db_username

  manage_master_user_password = true

  vpc_security_group_ids = [aws_security_group.postgres.id]
  db_subnet_group_name   = module.vpc.database_subnet_group_name

  backup_retention_period = 14
  deletion_protection     = true

  create_db_parameter_group = true
  family                    = "postgres16"

  # FinOps: RDS carries the full taxonomy (inherited) + explicit workload tag so
  # cost can be attributed per database in Cost Explorer / CUR.
  tags = {
    CostCenter = var.cost_center
    Workload   = var.workload
  }
}

# Security group allowing EKS worker nodes to reach Postgres on 5432.
resource "aws_security_group" "postgres" {
  name        = "ai-platform-${var.environment}-postgres"
  description = "Allow Postgres access from the EKS node group"
  vpc_id      = module.vpc.vpc_id
  tags = {
    CostCenter = var.cost_center
    Workload   = var.workload
  }
}

resource "aws_security_group_rule" "postgres_from_nodes" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = module.eks.node_security_group_id
  security_group_id        = aws_security_group.postgres.id
}