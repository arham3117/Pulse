output "queue_url" {
  description = "Checks queue URL."
  value       = aws_sqs_queue.checks.url
}

output "queue_name" {
  description = "Checks queue name."
  value       = aws_sqs_queue.checks.name
}

output "queue_arn" {
  description = "Checks queue ARN."
  value       = aws_sqs_queue.checks.arn
}

output "dlq_url" {
  description = "DLQ URL."
  value       = aws_sqs_queue.dlq.url
}

output "dlq_name" {
  description = "DLQ name."
  value       = aws_sqs_queue.dlq.name
}

output "dlq_arn" {
  description = "DLQ ARN."
  value       = aws_sqs_queue.dlq.arn
}
