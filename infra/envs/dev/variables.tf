variable "environment" {
  description = "Environment name."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-2"
}

variable "owner" {
  description = "Owner tag value."
  type        = string
}

variable "admin_cidrs" {
  description = "CIDRs allowed to reach the public EKS API endpoint (your public IP as /32). No default: set it in <env>.tfvars."
  type        = list(string)

  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for c in var.admin_cidrs : can(cidrhost(c, 0))])
    error_message = "admin_cidrs must be valid CIDRs. Replace the YOUR_IP/32 placeholder in the tfvars file with your public IP (curl -s https://checkip.amazonaws.com)/32."
  }

  validation {
    condition     = !contains(var.admin_cidrs, "0.0.0.0/0")
    error_message = "Do not open the EKS API to 0.0.0.0/0."
  }
}

variable "admin_principal_arns" {
  description = "IAM principals (e.g. your SSO admin role ARN) granted cluster-admin via EKS access entries."
  type        = list(string)
  default     = []
}

variable "enable_cluster_creator_admin_permissions" {
  description = "Give the identity that runs apply cluster-admin. Do not also list it in admin_principal_arns."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
variable "vpc_cidr" {
  description = "VPC CIDR."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Number of AZs."
  type        = number
  default     = 2
}

variable "single_nat_gateway" {
  description = "One shared NAT (dev) vs one per AZ (prod)."
  type        = bool
}

variable "enable_flow_logs" {
  description = "VPC flow logs to CloudWatch."
  type        = bool
}

variable "flow_log_retention_days" {
  description = "Flow log retention (days)."
  type        = number
}

# ---------------------------------------------------------------------------
# EKS
# ---------------------------------------------------------------------------
variable "kubernetes_version" {
  description = "EKS Kubernetes version."
  type        = string
}

variable "node_capacity_type" {
  description = "SPOT or ON_DEMAND."
  type        = string
}

variable "node_instance_types" {
  description = "Node instance types."
  type        = list(string)
}

variable "node_min_size" {
  description = "Node group min size."
  type        = number
}

variable "node_max_size" {
  description = "Node group max size."
  type        = number
}

variable "node_desired_size" {
  description = "Node group desired size."
  type        = number
}

variable "node_disk_size_gb" {
  description = "Node root volume size (GiB)."
  type        = number
  default     = 30
}

variable "cluster_log_retention_days" {
  description = "Control plane log retention (days)."
  type        = number
}

variable "kms_key_deletion_window_days" {
  description = "KMS key deletion window (days)."
  type        = number
}

# ---------------------------------------------------------------------------
# RDS
# ---------------------------------------------------------------------------
variable "db_engine_version" {
  description = "PostgreSQL major version."
  type        = string
  default     = "18"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
}

variable "db_allocated_storage" {
  description = "RDS initial storage (GiB)."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  description = "RDS storage autoscaling ceiling (GiB)."
  type        = number
  default     = 50
}

variable "db_multi_az" {
  description = "RDS Multi-AZ."
  type        = bool
}

variable "db_backup_retention_days" {
  description = "RDS backup retention (days)."
  type        = number
}

variable "db_deletion_protection" {
  description = "RDS deletion protection."
  type        = bool
}

variable "db_skip_final_snapshot" {
  description = "Skip RDS final snapshot on destroy."
  type        = bool
}

variable "db_performance_insights_enabled" {
  description = "RDS Performance Insights."
  type        = bool
}

variable "db_log_retention_days" {
  description = "RDS exported log retention (days)."
  type        = number
}

variable "db_apply_immediately" {
  description = "Apply RDS changes immediately."
  type        = bool
}

# ---------------------------------------------------------------------------
# Secrets / misc
# ---------------------------------------------------------------------------
variable "secret_recovery_window_days" {
  description = "Secrets Manager recovery window (0 = delete immediately)."
  type        = number
}

variable "ecr_repository_name" {
  description = "ECR repository created by infra/bootstrap."
  type        = string
  default     = "pulse"
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the ALB (empty = HTTP only on the ALB DNS name)."
  type        = string
  default     = ""
}
