variable "cluster_name" {
  description = "EKS cluster name, e.g. pulse-dev."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes minor version (latest in EKS standard support at build time)."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for the control plane ENIs and the node group."
  type        = list(string)
}

variable "admin_cidrs" {
  description = "CIDRs allowed to reach the public API endpoint (your IP /32). CI never talks to the cluster."
  type        = list(string)
}

variable "admin_principal_arns" {
  description = "IAM principal ARNs (e.g. your SSO admin role) granted AmazonEKSClusterAdminPolicy via access entries."
  type        = list(string)
  default     = []
}

variable "enable_cluster_creator_admin_permissions" {
  description = "Grant the identity running terraform apply cluster-admin via an access entry. Do not also list that ARN in admin_principal_arns."
  type        = bool
  default     = true
}

variable "node_capacity_type" {
  description = "SPOT (dev) or ON_DEMAND (prod)."
  type        = string

  validation {
    condition     = contains(["SPOT", "ON_DEMAND"], var.node_capacity_type)
    error_message = "node_capacity_type must be SPOT or ON_DEMAND."
  }
}

variable "node_instance_types" {
  description = "Instance types for the managed node group. Use several for spot availability."
  type        = list(string)
}

variable "node_min_size" {
  description = "Node group minimum size."
  type        = number
}

variable "node_max_size" {
  description = "Node group maximum size (Cluster Autoscaler ceiling)."
  type        = number
}

variable "node_desired_size" {
  description = "Node group initial desired size (Cluster Autoscaler owns it afterwards)."
  type        = number
}

variable "node_disk_size_gb" {
  description = "Root gp3 volume size per node."
  type        = number
  default     = 30
}

variable "cluster_log_retention_days" {
  description = "CloudWatch retention for control plane logs."
  type        = number
}

variable "kms_key_deletion_window_days" {
  description = "Deletion window for the secrets-encryption KMS key."
  type        = number
  default     = 7
}

variable "enable_network_policy" {
  description = "Enable Kubernetes NetworkPolicy enforcement in the VPC CNI."
  type        = bool
  default     = true
}

variable "ebs_csi_role_arn" {
  description = "IAM role for the EBS CSI controller (from workload-iam), bound to kube-system/ebs-csi-controller-sa via the add-on's Pod Identity association."
  type        = string
}
