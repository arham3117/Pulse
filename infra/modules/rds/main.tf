locals {
  major_version = split(".", var.engine_version)[0]
}

resource "aws_security_group" "db" {
  name        = "${var.identifier}-sg"
  description = "PostgreSQL access for ${var.identifier}: 5432 from EKS nodes only"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.identifier}-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "postgres" {
  for_each = toset(var.allowed_security_group_ids)

  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from ${each.value}"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = each.value
}
# No egress rules: RDS initiates no outbound connections.

module "db" {
  #checkov:skip=CKV_AWS_157:Multi-AZ is variable-driven: off in dev (cost), on in prod (prod.tfvars).
  #checkov:skip=CKV_AWS_293:Deletion protection is variable-driven: off in dev so it can be torn down every session, on in prod.
  #checkov:skip=CKV_AWS_161:Apps authenticate with the RDS-managed master secret via External Secrets; IAM DB auth is a stretch goal.
  #checkov:skip=CKV_AWS_353:Performance Insights is variable-driven and unsupported on some micro classes used in dev.
  #checkov:skip=CKV_AWS_354:Performance Insights (when enabled) uses the AWS-managed key; no CMK to keep cost down.
  #checkov:skip=CKV_AWS_118:Enhanced Monitoring adds a role and per-instance cost; CloudWatch basic metrics + exported logs are enough here.
  source  = "terraform-aws-modules/rds/aws"
  version = "7.2.2"

  identifier = var.identifier

  engine               = "postgres"
  engine_version       = var.engine_version
  family               = "postgres${local.major_version}"
  major_engine_version = local.major_version
  instance_class       = var.instance_class

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "pulse"
  username = "pulse"
  port     = 5432

  # RDS generates and rotates the master password in Secrets Manager; it never
  # appears in Terraform code or state.
  manage_master_user_password = true

  db_subnet_group_name   = var.db_subnet_group_name
  create_db_subnet_group = false
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  multi_az                 = var.multi_az
  backup_retention_period  = var.backup_retention_period
  deletion_protection      = var.deletion_protection
  skip_final_snapshot      = var.skip_final_snapshot
  copy_tags_to_snapshot    = true
  delete_automated_backups = var.skip_final_snapshot

  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false
  apply_immediately           = var.apply_immediately

  performance_insights_enabled          = var.performance_insights_enabled
  performance_insights_retention_period = var.performance_insights_enabled ? 7 : null

  enabled_cloudwatch_logs_exports        = ["postgresql", "upgrade"]
  create_cloudwatch_log_group            = true
  cloudwatch_log_group_retention_in_days = var.log_retention_days

  create_db_option_group    = false
  create_db_parameter_group = true
  parameter_group_name      = var.identifier
  parameters = [
    { name = "rds.force_ssl", value = "1" },                # reject non-TLS connections
    { name = "log_min_duration_statement", value = "500" }, # log queries slower than 500 ms
  ]
}
