variable "region" {
  description = "AWS region for every Pulse resource."
  type        = string
  default     = "us-east-2"
}

variable "owner" {
  description = "Value of the Owner tag."
  type        = string
  default     = "YOUR_NAME"
}

variable "github_owner" {
  description = "GitHub user or organisation that owns the pulse repository (used in the OIDC sub claims)."
  type        = string
  default     = "YOUR_GITHUB_USER"
}

variable "github_repo" {
  description = "Name of the application/infra repository."
  type        = string
  default     = "pulse"
}

variable "ecr_repository_name" {
  description = "ECR repository for the single Pulse image (shared by dev and prod)."
  type        = string
  default     = "pulse"
}

variable "ecr_keep_tagged_images" {
  description = "Number of tagged images to keep in ECR."
  type        = number
  default     = 30
}

variable "budget_email" {
  description = "Email address that receives AWS Budgets (and optional cost anomaly) notifications. No default on purpose: pass it with -var or TF_VAR_budget_email."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_email))
    error_message = "budget_email must be a valid email address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD. Notifications fire at 50%, 80% and 100% (actual) and 100% (forecasted)."
  type        = number
  default     = 100

  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be positive."
  }
}

variable "enable_cost_anomaly_detection" {
  description = "Create a Cost Anomaly Detection service monitor + daily email subscription. AWS allows only one SERVICE monitor per account, so leave false if you already have one."
  type        = bool
  default     = false
}

variable "cost_anomaly_threshold_usd" {
  description = "Minimum total impact (USD) of an anomaly before an email is sent."
  type        = number
  default     = 10
}
