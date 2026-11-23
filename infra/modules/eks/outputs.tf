output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_arn" {
  description = "EKS cluster ARN."
  value       = module.eks.cluster_arn
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint."
  value       = module.eks.cluster_endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64 cluster CA."
  value       = module.eks.cluster_certificate_authority_data
}

output "cluster_version" {
  description = "Kubernetes version."
  value       = module.eks.cluster_version
}

output "cluster_security_group_id" {
  description = "Cluster (control plane) security group ID."
  value       = module.eks.cluster_security_group_id
}

output "node_security_group_id" {
  description = "Node security group ID (pods share it; RDS allows 5432 from it)."
  value       = module.eks.node_security_group_id
}

output "kms_key_arn" {
  description = "KMS key used for Kubernetes Secrets envelope encryption."
  value       = module.eks.kms_key_arn
}

output "node_group_autoscaling_group_names" {
  description = "ASG names backing the managed node group."
  value       = module.eks.eks_managed_node_groups_autoscaling_group_names
}
