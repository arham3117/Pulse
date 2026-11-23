# Secret CONTAINERS only. Values are set out of band with
#   aws secretsmanager put-secret-value --secret-id pulse/<env>/app --secret-string file://...
# so secret values never enter Terraform code or state (docs/runbooks/set-secrets.md).
locals {
  secrets = {
    app = {
      name        = "pulse/${var.environment}/app"
      description = "Pulse app secrets. JSON keys: admin_api_key, alert_webhook_url"
    }
    grafana = {
      name        = "pulse/${var.environment}/grafana"
      description = "Grafana admin. JSON keys: admin_password"
    }
    alertmanager = {
      name        = "pulse/${var.environment}/alertmanager"
      description = "Alertmanager platform alerts channel. JSON keys: webhook_url"
    }
  }
}

resource "aws_secretsmanager_secret" "this" {
  #checkov:skip=CKV_AWS_149:AWS-managed key (aws/secretsmanager) is sufficient; a CMK would need key-policy grants for External Secrets and adds monthly cost.
  #checkov:skip=CKV2_AWS_57:Values are third-party webhook URLs and a manually chosen admin key; Lambda rotation does not apply. Rotate by put-secret-value.
  for_each = local.secrets

  name                    = each.value.name
  description             = each.value.description
  recovery_window_in_days = var.recovery_window_in_days
}
