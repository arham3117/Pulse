# prod: exists to prove the HA configuration works. Bring it up for a demo, then destroy it.

environment = "prod"
region      = "us-east-2"
owner       = "YOUR_NAME"

# Your public IP as /32 (curl -s https://checkip.amazonaws.com). Plan fails until replaced.
admin_cidrs = ["YOUR_IP/32"]

# Optional: your SSO admin role ARN.
admin_principal_arns = []

# Network: one NAT gateway per AZ (no cross-AZ egress dependency).
single_nat_gateway      = false
enable_flow_logs        = true
flow_log_retention_days = 14

# EKS: on-demand, spread across both AZs.
kubernetes_version           = "1.37"
node_capacity_type           = "ON_DEMAND"
node_instance_types          = ["m5.large"]
node_min_size                = 2
node_max_size                = 6
node_desired_size            = 3
node_disk_size_gb            = 50
cluster_log_retention_days   = 30
kms_key_deletion_window_days = 30

# RDS: Multi-AZ, protected, final snapshot on destroy.
db_instance_class               = "db.t4g.small"
db_multi_az                     = true
db_backup_retention_days        = 7
db_deletion_protection          = true
db_skip_final_snapshot          = false
db_performance_insights_enabled = false # PI needs a larger class on some engines; enable on db.t4g.medium+
db_log_retention_days           = 30
db_apply_immediately            = false

secret_recovery_window_days = 7

acm_certificate_arn = ""
