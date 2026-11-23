output "vpc_id" {
  description = "VPC ID."
  value       = module.vpc.vpc_id
}

output "vpc_cidr_block" {
  description = "VPC CIDR block."
  value       = module.vpc.vpc_cidr_block
}

output "azs" {
  description = "Availability zones in use."
  value       = local.azs
}

output "public_subnet_ids" {
  description = "Public subnet IDs (ALB, NAT)."
  value       = module.vpc.public_subnets
}

output "private_subnet_ids" {
  description = "Private subnet IDs (EKS nodes and pods)."
  value       = module.vpc.private_subnets
}

output "database_subnet_ids" {
  description = "Database subnet IDs."
  value       = module.vpc.database_subnets
}

output "database_subnet_group_name" {
  description = "RDS DB subnet group name."
  value       = module.vpc.database_subnet_group_name
}

output "nat_public_ips" {
  description = "NAT gateway public IPs (egress IPs for health checks)."
  value       = module.vpc.nat_public_ips
}
