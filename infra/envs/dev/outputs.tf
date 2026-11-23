# Output names are fixed by docs/CONTRACT.md and consumed by
# scripts/render-infra-values.sh. Same names in every environment.

output "region" {
  description = "AWS region."
  value       = var.region
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "ecr_repository_url" {
  description = "ECR repository URL (from bootstrap, via data source)."
  value       = data.aws_ecr_repository.pulse.repository_url
}

output "sqs_queue_url" {
  description = "Checks queue URL."
  value       = module.sqs.queue_url
}

output "sqs_queue_name" {
  description = "Checks queue name."
  value       = module.sqs.queue_name
}

output "sqs_dlq_url" {
  description = "Checks DLQ URL."
  value       = module.sqs.dlq_url
}

output "db_host" {
  description = "RDS endpoint hostname."
  value       = module.rds.db_host
}

output "db_port" {
  description = "RDS port."
  value       = module.rds.db_port
}

output "db_name" {
  description = "Database name."
  value       = module.rds.db_name
}

output "rds_master_secret_arn" {
  description = "ARN of the RDS-managed master user secret (read by External Secrets)."
  value       = module.rds.master_user_secret_arn
}

output "app_secret_name" {
  description = "Secrets Manager name of the app secret."
  value       = module.secrets.app_secret_name
}

output "grafana_secret_name" {
  description = "Secrets Manager name of the Grafana secret."
  value       = module.secrets.grafana_secret_name
}

output "alertmanager_secret_name" {
  description = "Secrets Manager name of the Alertmanager secret."
  value       = module.secrets.alertmanager_secret_name
}

output "acm_certificate_arn" {
  description = "ACM certificate ARN for the ALB (empty string if none)."
  value       = var.acm_certificate_arn
}

output "vpc_id" {
  description = "VPC ID (the AWS Load Balancer Controller needs it because pods cannot reach IMDS)."
  value       = module.network.vpc_id
}

output "workload_role_arns" {
  description = "namespace/serviceaccount => Pod Identity role ARN (informational)."
  value       = module.workload_iam.pod_identity_associations
}
