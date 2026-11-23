# GitHub Actions OIDC identity provider: CI gets short-lived credentials, no stored AWS keys.
# AWS validates GitHub's OIDC certificate chain itself, so no thumbprint is required.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
