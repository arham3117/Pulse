variable "environment" {
  description = "Environment name (dev|prod); secrets are named pulse/<env>/<name>."
  type        = string
}

variable "recovery_window_in_days" {
  description = "Days before a deleted secret is purged. 0 (dev) allows destroy + re-create with the same name in one session; you must re-put the values after each rebuild."
  type        = number
  default     = 7
}
