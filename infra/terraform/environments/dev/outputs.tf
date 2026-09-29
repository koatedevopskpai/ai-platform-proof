output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = module.eks.cluster_endpoint
  sensitive   = true
}

output "postgres_address" {
  description = "RDS Postgres endpoint"
  value       = module.postgres.db_instance_address
  sensitive   = true
}

output "postgres_db_name" {
  value = module.postgres.db_instance_name
}

output "configure_kubectl" {
  description = "Command to configure kubectl"
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region}"
}