locals {
  admin_access_entries = {
    for idx, arn in var.admin_principal_arns : "admin-${idx}" => {
      principal_arn = arn
      policy_associations = {
        cluster_admin = {
          policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = {
            type = "cluster"
          }
        }
      }
    }
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.30.2"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  vpc_id     = var.vpc_id
  subnet_ids = var.subnet_ids

  # API endpoint: private access for nodes, public access only from admin_cidrs.
  endpoint_private_access      = true
  endpoint_public_access       = true
  endpoint_public_access_cidrs = var.admin_cidrs

  # Access entries only (no aws-auth ConfigMap).
  authentication_mode                      = "API"
  enable_cluster_creator_admin_permissions = var.enable_cluster_creator_admin_permissions
  access_entries                           = local.admin_access_entries

  # Workloads use EKS Pod Identity, so no IRSA OIDC provider.
  enable_irsa = false

  # Envelope encryption of Kubernetes Secrets with a module-managed KMS key.
  create_kms_key                  = true
  kms_key_deletion_window_in_days = var.kms_key_deletion_window_days
  enable_kms_key_rotation         = true
  encryption_config = {
    resources = ["secrets"]
  }

  enabled_log_types                      = ["api", "audit", "authenticator"]
  create_cloudwatch_log_group            = true
  cloudwatch_log_group_retention_in_days = var.cluster_log_retention_days

  addons = {
    vpc-cni = {
      before_compute = true
      configuration_values = jsonencode({
        enableNetworkPolicy = var.enable_network_policy ? "true" : "false"
        env = {
          # Prefix delegation: each ENI slot gets a /28, so small nodes run many more pods.
          ENABLE_PREFIX_DELEGATION = "true"
          WARM_PREFIX_TARGET       = "1"
        }
      })
    }
    eks-pod-identity-agent = {
      before_compute = true
    }
    kube-proxy = {}
    coredns    = {}
    aws-ebs-csi-driver = {
      pod_identity_association = [{
        role_arn        = var.ebs_csi_role_arn
        service_account = "ebs-csi-controller-sa"
      }]
      configuration_values = jsonencode({
        controller = {
          # Tags every dynamically provisioned volume with kubernetes.io/cluster/<name>=owned
          # so scripts/pre-destroy-cleanup.sh can find leftovers.
          k8sTagClusterId = var.cluster_name
        }
      })
    }
  }

  eks_managed_node_groups = {
    default = {
      name            = "${var.cluster_name}-ng"
      use_name_prefix = true

      ami_type       = "AL2023_x86_64_STANDARD"
      capacity_type  = var.node_capacity_type
      instance_types = var.node_instance_types

      min_size     = var.node_min_size
      max_size     = var.node_max_size
      desired_size = var.node_desired_size

      subnet_ids = var.subnet_ids

      iam_role_name            = "${var.cluster_name}-node"
      iam_role_use_name_prefix = true

      # IMDSv2 only, hop limit 1: pods (one extra network hop) cannot reach the
      # node's instance-role credentials. Workloads get AWS access via Pod Identity.
      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 1
      }

      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = var.node_disk_size_gb
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }

      labels = {
        "pulse.io/capacity-type" = lower(var.node_capacity_type)
      }

      update_config = {
        max_unavailable_percentage = 33
      }
    }
  }
}
