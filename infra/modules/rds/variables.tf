variable "identifier" {
  description = "RDS instance identifier, e.g. pulse-dev-db."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for the database security group."
  type        = string
}

variable "db_subnet_group_name" {
  description = "DB subnet group (database subnets, no internet route)."
  type        = string
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to connect on 5432 (the EKS node security group)."
  type        = list(string)
}

variable "engine_version" {
  description = "PostgreSQL major version. Minor versions are auto-upgraded in the maintenance window."
  type        = string
  default     = "18"
}

variable "instance_class" {
  description = "DB instance class."
  type        = string
}

variable "allocated_storage" {
  description = "Initial gp3 storage in GiB."
  type        = number
  default     = 20
}

variable "max_allocated_storage" {
  description = "Storage autoscaling ceiling in GiB."
  type        = number
  default     = 50
}

variable "multi_az" {
  description = "Multi-AZ standby (prod)."
  type        = bool
}

variable "backup_retention_period" {
  description = "Automated backup retention in days."
  type        = number
}

variable "deletion_protection" {
  description = "Block deletion of the instance."
  type        = bool
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot on destroy (dev only)."
  type        = bool
}

variable "performance_insights_enabled" {
  description = "Enable Performance Insights (7-day free tier). Not available on every micro class."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch retention for exported PostgreSQL logs."
  type        = number
  default     = 7
}

variable "apply_immediately" {
  description = "Apply modifications immediately instead of in the maintenance window."
  type        = bool
  default     = false
}
