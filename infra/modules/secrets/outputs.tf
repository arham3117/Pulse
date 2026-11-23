output "app_secret_name" {
  description = "Name of the app secret."
  value       = aws_secretsmanager_secret.this["app"].name
}

output "grafana_secret_name" {
  description = "Name of the Grafana secret."
  value       = aws_secretsmanager_secret.this["grafana"].name
}

output "alertmanager_secret_name" {
  description = "Name of the Alertmanager secret."
  value       = aws_secretsmanager_secret.this["alertmanager"].name
}

output "secret_arns" {
  description = "Map of secret key => ARN."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}
