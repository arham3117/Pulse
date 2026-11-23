locals {
  repo = "repo:${var.github_owner}/${var.github_repo}"

  # Exact `sub` claims per role. StringEquals (not StringLike): never wildcard the repo or ref.
  ci_role_subjects = {
    "pulse-ci-ecr-push" = [
      "${local.repo}:ref:refs/heads/main",
    ]
    "pulse-ci-tf-plan" = [
      "${local.repo}:pull_request",
      "${local.repo}:ref:refs/heads/main",
    ]
    "pulse-ci-tf-apply" = [
      "${local.repo}:environment:dev",
      "${local.repo}:environment:prod",
    ]
  }
}

data "aws_iam_policy_document" "ci_trust" {
  for_each = local.ci_role_subjects

  statement {
    sid     = "GitHubActionsOIDC"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = each.value
    }
  }
}

resource "aws_iam_role" "ci" {
  for_each = local.ci_role_subjects

  name                 = each.key
  description          = "GitHub Actions OIDC role for ${var.github_owner}/${var.github_repo} (${each.key})"
  assume_role_policy   = data.aws_iam_policy_document.ci_trust[each.key].json
  max_session_duration = 3600
}

# ---------------------------------------------------------------------------
# pulse-ci-ecr-push: push/pull + describe on the pulse repo only.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ecr_push" {
  statement {
    sid       = "EcrAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # GetAuthorizationToken does not support resource-level permissions.
  }

  statement {
    sid    = "PushPullDescribePulseRepo"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImageScanFindings",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:ListImages",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.pulse.arn]
  }
}

resource "aws_iam_role_policy" "ecr_push" {
  name   = "ecr-push-pulse"
  role   = aws_iam_role.ci["pulse-ci-ecr-push"].id
  policy = data.aws_iam_policy_document.ecr_push.json
}

# ---------------------------------------------------------------------------
# State bucket access, shared by the plan and apply roles. Plan needs write
# access because `use_lockfile = true` writes a .tflock object.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "tfstate_rw" {
  statement {
    sid       = "ListStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.tfstate.arn]
  }

  statement {
    sid       = "ReadWriteEnvState"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/envs/*"]
  }
}

resource "aws_iam_role_policy" "tf_plan_state" {
  name   = "tfstate-rw"
  role   = aws_iam_role.ci["pulse-ci-tf-plan"].id
  policy = data.aws_iam_policy_document.tfstate_rw.json
}

resource "aws_iam_role_policy_attachment" "tf_plan_readonly" {
  role       = aws_iam_role.ci["pulse-ci-tf-plan"].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/ReadOnlyAccess"
}

# ---------------------------------------------------------------------------
# pulse-ci-tf-apply: deliberately broad to start (plan 3.1), tighten in Phase 7
# with IAM Access Analyzer policy generation. PowerUserAccess covers every
# non-IAM service; IAM writes are limited to pulse-* names in this account.
# ---------------------------------------------------------------------------
resource "aws_iam_role_policy" "tf_apply_state" {
  name   = "tfstate-rw"
  role   = aws_iam_role.ci["pulse-ci-tf-apply"].id
  policy = data.aws_iam_policy_document.tfstate_rw.json
}

resource "aws_iam_role_policy_attachment" "tf_apply_poweruser" {
  role       = aws_iam_role.ci["pulse-ci-tf-apply"].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "tf_apply_iam" {
  #checkov:skip=CKV_AWS_356:iam:CreateServiceLinkedRole and the read-only IAM calls do not support resource scoping beyond the account.
  #checkov:skip=CKV_AWS_111:Write access is constrained to pulse-* role/policy ARNs; the remaining wildcard statement is service-linked-role creation only.
  statement {
    sid    = "ManagePulseRolesAndPolicies"
    effect = "Allow"
    actions = [
      "iam:AttachRolePolicy",
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:CreateRole",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PassRole",
      "iam:PutRolePolicy",
      "iam:TagPolicy",
      "iam:TagRole",
      "iam:UntagPolicy",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ]
    resources = [
      "arn:${local.partition}:iam::${local.account_id}:role/pulse-*",
      "arn:${local.partition}:iam::${local.account_id}:policy/pulse-*",
    ]
  }

  statement {
    sid    = "ServiceLinkedRoles"
    effect = "Allow"
    actions = [
      "iam:CreateServiceLinkedRole",
    ]
    resources = ["*"]

    condition {
      test     = "StringLike"
      variable = "iam:AWSServiceName"
      values = [
        "eks.amazonaws.com",
        "eks-nodegroup.amazonaws.com",
        "autoscaling.amazonaws.com",
        "elasticloadbalancing.amazonaws.com",
        "rds.amazonaws.com",
        "spot.amazonaws.com",
      ]
    }
  }
}

resource "aws_iam_role_policy" "tf_apply_iam" {
  name   = "iam-pulse-scoped"
  role   = aws_iam_role.ci["pulse-ci-tf-apply"].id
  policy = data.aws_iam_policy_document.tf_apply_iam.json
}
