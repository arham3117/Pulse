output "role_arns" {
  description = "Map of workload => IAM role ARN."
  value       = { for k, r in aws_iam_role.workload : k => r.arn }
}

output "ebs_csi_role_arn" {
  description = "Role for the EBS CSI controller (wired into the aws-ebs-csi-driver add-on)."
  value       = aws_iam_role.workload["ebs-csi-driver"].arn
}

output "pod_identity_associations" {
  description = "namespace/serviceaccount => role ARN for every Pod Identity binding (incl. the add-on managed EBS CSI one)."
  value = {
    for k, v in local.workloads : "${v.namespace}/${v.service_account}" => aws_iam_role.workload[k].arn
  }
}
