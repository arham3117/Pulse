variable "name_prefix" {
  description = "Prefix for role names. Must equal the EKS cluster name (pulse-<env>): it is also used to build the cluster ARN in the trust policy."
  type        = string
}

variable "environment" {
  description = "Environment name (dev|prod)."
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name. Must be the module.eks output so associations are created after the cluster."
  type        = string
}

variable "checks_queue_arn" {
  description = "ARN of the checks queue."
  type        = string
}

variable "checks_dlq_arn" {
  description = "ARN of the checks DLQ."
  type        = string
}

variable "rds_master_secret_arn" {
  description = "ARN of the RDS-managed master user secret."
  type        = string
}
