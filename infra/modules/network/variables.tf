variable "name" {
  description = "Name prefix, e.g. pulse-dev."
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name (used for subnet discovery tags)."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones (2 is enough for this project; prod could use 3 for more headroom)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3 (RDS subnet groups need at least 2 AZs)."
  }
}

variable "single_nat_gateway" {
  description = "true = one shared NAT gateway (dev, cheaper, AZ-level SPOF); false = one NAT per AZ (prod, HA)."
  type        = bool
}

variable "enable_flow_logs" {
  description = "Ship VPC flow logs to CloudWatch Logs."
  type        = bool
  default     = true
}

variable "flow_log_retention_days" {
  description = "Retention for the flow log group."
  type        = number
  default     = 7
}
