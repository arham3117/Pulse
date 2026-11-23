output "state_bucket_name" {
  description = "Terraform state bucket. Put it in infra/envs/*/backend.tf (replace pulse-tfstate-ACCOUNT_ID)."
  value       = aws_s3_bucket.tfstate.bucket
}

output "ecr_repository_url" {
  description = "ECR repository URL for the pulse image."
  value       = aws_ecr_repository.pulse.repository_url
}

output "ecr_repository_arn" {
  description = "ECR repository ARN."
  value       = aws_ecr_repository.pulse.arn
}

output "github_oidc_provider_arn" {
  description = "GitHub Actions OIDC provider ARN."
  value       = aws_iam_openid_connect_provider.github.arn
}

output "ci_ecr_push_role_arn" {
  description = "Role for app-ci push/promote-prod verify. Store as GitHub repository variable."
  value       = aws_iam_role.ci["pulse-ci-ecr-push"].arn
}

output "ci_tf_plan_role_arn" {
  description = "Role for terraform plan in CI. Store as GitHub repository variable."
  value       = aws_iam_role.ci["pulse-ci-tf-plan"].arn
}

output "ci_tf_apply_role_arn" {
  description = "Role for terraform apply in CI (environment-gated). Store as GitHub repository variable."
  value       = aws_iam_role.ci["pulse-ci-tf-apply"].arn
}

output "aws_region" {
  description = "Region the bootstrap resources live in."
  value       = var.region
}
