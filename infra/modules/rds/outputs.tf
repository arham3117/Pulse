output "db_host" {
  description = "RDS endpoint hostname."
  value       = module.db.db_instance_address
}

output "db_port" {
  description = "RDS port."
  value       = module.db.db_instance_port
}

output "db_name" {
  description = "Database name."
  value       = module.db.db_instance_name
}

output "db_instance_identifier" {
  description = "RDS instance identifier."
  value       = module.db.db_instance_identifier
}

output "master_user_secret_arn" {
  description = "ARN of the RDS-managed master user secret (username + password)."
  value       = module.db.db_instance_master_user_secret_arn
}

output "security_group_id" {
  description = "Database security group ID."
  value       = aws_security_group.db.id
}
