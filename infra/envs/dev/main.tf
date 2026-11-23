locals {
  name = "pulse-${var.environment}" # also the EKS cluster name (docs/CONTRACT.md)
}

# Created once by infra/bootstrap and shared by every environment.
data "aws_ecr_repository" "pulse" {
  name = var.ecr_repository_name
}

module "network" {
  source = "../../modules/network"

  name                    = local.name
  cluster_name            = local.name
  vpc_cidr                = var.vpc_cidr
  az_count                = var.az_count
  single_nat_gateway      = var.single_nat_gateway
  enable_flow_logs        = var.enable_flow_logs
  flow_log_retention_days = var.flow_log_retention_days
}

module "eks" {
  source = "../../modules/eks"

  cluster_name       = local.name
  kubernetes_version = var.kubernetes_version
  vpc_id             = module.network.vpc_id
  subnet_ids         = module.network.private_subnet_ids

  admin_cidrs                              = var.admin_cidrs
  admin_principal_arns                     = var.admin_principal_arns
  enable_cluster_creator_admin_permissions = var.enable_cluster_creator_admin_permissions

  node_capacity_type  = var.node_capacity_type
  node_instance_types = var.node_instance_types
  node_min_size       = var.node_min_size
  node_max_size       = var.node_max_size
  node_desired_size   = var.node_desired_size
  node_disk_size_gb   = var.node_disk_size_gb

  cluster_log_retention_days   = var.cluster_log_retention_days
  kms_key_deletion_window_days = var.kms_key_deletion_window_days

  ebs_csi_role_arn = module.workload_iam.ebs_csi_role_arn
}

module "sqs" {
  source = "../../modules/sqs"

  name = "${local.name}-checks"
}

module "secrets" {
  source = "../../modules/secrets"

  environment             = var.environment
  recovery_window_in_days = var.secret_recovery_window_days
}

module "rds" {
  source = "../../modules/rds"

  identifier                 = "${local.name}-db"
  vpc_id                     = module.network.vpc_id
  db_subnet_group_name       = module.network.database_subnet_group_name
  allowed_security_group_ids = [module.eks.node_security_group_id]

  engine_version               = var.db_engine_version
  instance_class               = var.db_instance_class
  allocated_storage            = var.db_allocated_storage
  max_allocated_storage        = var.db_max_allocated_storage
  multi_az                     = var.db_multi_az
  backup_retention_period      = var.db_backup_retention_days
  deletion_protection          = var.db_deletion_protection
  skip_final_snapshot          = var.db_skip_final_snapshot
  performance_insights_enabled = var.db_performance_insights_enabled
  log_retention_days           = var.db_log_retention_days
  apply_immediately            = var.db_apply_immediately
}

module "workload_iam" {
  source = "../../modules/workload-iam"

  name_prefix           = local.name
  environment           = var.environment
  cluster_name          = module.eks.cluster_name
  checks_queue_arn      = module.sqs.queue_arn
  checks_dlq_arn        = module.sqs.dlq_arn
  rds_master_secret_arn = module.rds.master_user_secret_arn
}
