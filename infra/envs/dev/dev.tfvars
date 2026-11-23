# dev: cheapest configuration that still exercises every moving part.
# Runs only while you work: `make infra-down ENV=dev` at the end of every session.

environment = "dev"
region      = "us-east-2"
owner       = "YOUR_NAME"

# Your public IP as /32 (curl -s https://checkip.amazonaws.com). Plan fails until replaced.
admin_cidrs = ["YOUR_IP/32"]

# Optional: your SSO admin role ARN, e.g.
# "arn:aws:iam::ACCOUNT_ID:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_AdministratorAccess_xxxx"
admin_principal_arns = []

# Network: one NAT gateway (cost; an AZ outage takes out egress for the other AZ).
single_nat_gateway      = true
enable_flow_logs        = true
flow_log_retention_days = 3

# EKS: spot, several instance families for spot availability.
kubernetes_version           = "1.37"
node_capacity_type           = "SPOT"
node_instance_types          = ["t3.large", "t3a.large", "m5.large", "m5a.large"]
node_min_size                = 2
node_max_size                = 4
node_desired_size            = 2
node_disk_size_gb            = 30
cluster_log_retention_days   = 3
kms_key_deletion_window_days = 7

# RDS: smallest class, single AZ, disposable.
db_instance_class               = "db.t4g.micro"
db_multi_az                     = false
db_backup_retention_days        = 1
db_deletion_protection          = false
db_skip_final_snapshot          = true
db_performance_insights_enabled = false
db_log_retention_days           = 3
db_apply_immediately            = true

# Secrets: delete immediately so the env can be rebuilt with the same names.
# Re-run the put-secret-value commands (docs/runbooks/set-secrets.md) after each rebuild.
secret_recovery_window_days = 0

acm_certificate_arn = ""
