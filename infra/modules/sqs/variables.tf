variable "name" {
  description = "Queue base name, e.g. pulse-dev-checks (DLQ gets the -dlq suffix)."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "Visibility timeout; must exceed the longest possible check."
  type        = number
  default     = 60
}

variable "message_retention_seconds" {
  description = "Main queue retention. 1 hour: a check that is an hour late is useless."
  type        = number
  default     = 3600
}

variable "dlq_message_retention_seconds" {
  description = "DLQ retention. 4 days gives time to investigate poison messages."
  type        = number
  default     = 345600
}

variable "max_receive_count" {
  description = "Receives before a message moves to the DLQ."
  type        = number
  default     = 3
}
