data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region

  # Built from the name (not the cluster resource) so the EBS CSI role has no
  # dependency on the cluster: the eks module consumes that role in its add-on.
  cluster_arn = "arn:${local.partition}:eks:${local.region}:${local.account_id}:cluster/${var.name_prefix}"

  # One IAM role per Kubernetes service account (docs/CONTRACT.md).
  # association = false: the association is created by the EKS add-on instead
  # (aws-ebs-csi-driver in modules/eks), so pods start with credentials.
  workloads = {
    scheduler = {
      namespace       = "pulse"
      service_account = "pulse-scheduler"
      association     = true
    }
    worker = {
      namespace       = "pulse"
      service_account = "pulse-worker"
      association     = true
    }
    external-secrets = {
      namespace       = "external-secrets"
      service_account = "external-secrets"
      association     = true
    }
    keda-operator = {
      namespace       = "keda"
      service_account = "keda-operator"
      association     = true
    }
    aws-load-balancer-controller = {
      namespace       = "kube-system"
      service_account = "aws-load-balancer-controller"
      association     = true
    }
    ebs-csi-driver = {
      namespace       = "kube-system"
      service_account = "ebs-csi-controller-sa"
      association     = false
    }
    cluster-autoscaler = {
      namespace       = "kube-system"
      service_account = "cluster-autoscaler"
      association     = true
    }
  }
}

# ---------------------------------------------------------------------------
# Pod Identity trust: only the EKS Pod Identity service, only for this
# account and this cluster.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    sid     = "EksPodIdentity"
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [local.cluster_arn]
    }
  }
}

resource "aws_iam_role" "workload" {
  for_each = local.workloads

  name               = "${var.name_prefix}-${each.key}"
  description        = "Pod Identity role for ${each.value.namespace}/${each.value.service_account}"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_eks_pod_identity_association" "workload" {
  for_each = { for k, v in local.workloads : k => v if v.association }

  cluster_name    = var.cluster_name
  namespace       = each.value.namespace
  service_account = each.value.service_account
  role_arn        = aws_iam_role.workload[each.key].arn
}

# ---------------------------------------------------------------------------
# pulse/pulse-scheduler
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "EnqueueChecks"
    effect    = "Allow"
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [var.checks_queue_arn]
  }

  statement {
    sid       = "ReadDlqDepth"
    effect    = "Allow"
    actions   = ["sqs:GetQueueAttributes"]
    resources = [var.checks_dlq_arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "sqs-enqueue"
  role   = aws_iam_role.workload["scheduler"].id
  policy = data.aws_iam_policy_document.scheduler.json
}

# ---------------------------------------------------------------------------
# pulse/pulse-worker
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "worker" {
  statement {
    sid    = "ConsumeChecks"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [var.checks_queue_arn]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "sqs-consume"
  role   = aws_iam_role.workload["worker"].id
  policy = data.aws_iam_policy_document.worker.json
}

# ---------------------------------------------------------------------------
# external-secrets/external-secrets
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "external_secrets" {
  statement {
    sid     = "ReadPulseSecrets"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [
      "arn:${local.partition}:secretsmanager:${local.region}:${local.account_id}:secret:pulse/${var.environment}/*",
      var.rds_master_secret_arn,
    ]
  }
}

resource "aws_iam_role_policy" "external_secrets" {
  name   = "secretsmanager-read"
  role   = aws_iam_role.workload["external-secrets"].id
  policy = data.aws_iam_policy_document.external_secrets.json
}

# ---------------------------------------------------------------------------
# keda/keda-operator
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "keda" {
  statement {
    sid       = "ReadQueueDepth"
    effect    = "Allow"
    actions   = ["sqs:GetQueueAttributes"]
    resources = [var.checks_queue_arn]
  }
}

resource "aws_iam_role_policy" "keda" {
  name   = "sqs-queue-depth"
  role   = aws_iam_role.workload["keda-operator"].id
  policy = data.aws_iam_policy_document.keda.json
}

# ---------------------------------------------------------------------------
# kube-system/aws-load-balancer-controller
# Official policy vendored from kubernetes-sigs/aws-load-balancer-controller
# at tag v3.6.3 (docs/install/iam_policy.json). Bump the file together with the
# controller chart version.
# ---------------------------------------------------------------------------
resource "aws_iam_policy" "aws_load_balancer_controller" {
  #checkov:skip=CKV_AWS_290:Vendored upstream policy; the controller discovers and creates ELB/EC2 resources whose ARNs are not known in advance. Mutations are tag-conditioned upstream.
  #checkov:skip=CKV_AWS_355:Vendored upstream policy; Describe*/Get* calls do not support resource-level permissions.
  #checkov:skip=CKV_AWS_289:Vendored upstream policy; permission management is limited to security groups the controller tags itself.
  name        = "${var.name_prefix}-aws-load-balancer-controller"
  description = "AWS Load Balancer Controller v3.6.3 (vendored upstream policy)"
  policy      = jsonencode(jsondecode(file("${path.module}/policies/aws-load-balancer-controller-v3.6.3.json")))
}

resource "aws_iam_role_policy_attachment" "aws_load_balancer_controller" {
  role       = aws_iam_role.workload["aws-load-balancer-controller"].name
  policy_arn = aws_iam_policy.aws_load_balancer_controller.arn
}

# ---------------------------------------------------------------------------
# kube-system/ebs-csi-controller-sa
# ---------------------------------------------------------------------------
resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.workload["ebs-csi-driver"].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

# ---------------------------------------------------------------------------
# kube-system/cluster-autoscaler
# Mutations only on ASGs tagged for THIS cluster (EKS managed node groups add
# k8s.io/cluster-autoscaler/<cluster>=owned automatically).
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "cluster_autoscaler" {
  #checkov:skip=CKV_AWS_356:Autoscaling/EC2 Describe* calls do not support resource-level permissions.
  statement {
    sid    = "Discover"
    effect = "Allow"
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:DescribeLaunchConfigurations",
      "autoscaling:DescribeScalingActivities",
      "autoscaling:DescribeTags",
      "ec2:DescribeImages",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeLaunchTemplateVersions",
      "ec2:GetInstanceTypesFromInstanceRequirements",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ScaleOwnNodeGroups"
    effect = "Allow"
    actions = [
      "autoscaling:SetDesiredCapacity",
      "autoscaling:TerminateInstanceInAutoScalingGroup",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/k8s.io/cluster-autoscaler/${var.cluster_name}"
      values   = ["owned"]
    }
  }

  statement {
    sid       = "DescribeOwnNodeGroups"
    effect    = "Allow"
    actions   = ["eks:DescribeNodegroup"]
    resources = ["arn:${local.partition}:eks:${local.region}:${local.account_id}:nodegroup/${var.name_prefix}/*/*"]
  }
}

resource "aws_iam_role_policy" "cluster_autoscaler" {
  name   = "autoscaling-own-cluster"
  role   = aws_iam_role.workload["cluster-autoscaler"].id
  policy = data.aws_iam_policy_document.cluster_autoscaler.json
}
