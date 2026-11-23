data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_region" "current" {}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # 10.20.0.0/16 layout:
  #   public   /24  10.20.0.0  - 10.20.2.0     ALB + NAT
  #   private  /20  10.20.16.0 - 10.20.48.0    nodes + pods (VPC CNI hands pods VPC IPs; prefix delegation eats /28s)
  #   database /24  10.20.200.0 - 10.20.202.0  RDS only, no route to the internet
  public_subnets   = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnets  = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 4, i + 1)]
  database_subnets = [for i in range(var.az_count) : cidrsubnet(var.vpc_cidr, 8, 200 + i)]
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.8.0"

  name = var.name
  cidr = var.vpc_cidr
  azs  = local.azs

  public_subnets   = local.public_subnets
  private_subnets  = local.private_subnets
  database_subnets = local.database_subnets

  enable_dns_hostnames    = true
  enable_dns_support      = true
  map_public_ip_on_launch = false

  # NAT: single (dev, cost) vs one per AZ (prod, HA). See ADR on NAT strategy.
  enable_nat_gateway     = true
  single_nat_gateway     = var.single_nat_gateway
  one_nat_gateway_per_az = !var.single_nat_gateway

  # Database subnets: own route table, no IGW and no NAT route.
  create_database_subnet_group           = true
  create_database_subnet_route_table     = true
  create_database_internet_gateway_route = false
  create_database_nat_gateway_route      = false

  # Lock down the default security group (no rules).
  manage_default_security_group  = true
  default_security_group_ingress = []
  default_security_group_egress  = []

  # Subnet discovery for the AWS Load Balancer Controller.
  public_subnet_tags = {
    "kubernetes.io/role/elb"                    = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb"           = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  }

  enable_flow_log                                 = var.enable_flow_logs
  create_flow_log_cloudwatch_log_group            = var.enable_flow_logs
  create_flow_log_cloudwatch_iam_role             = var.enable_flow_logs
  flow_log_cloudwatch_log_group_retention_in_days = var.flow_log_retention_days
  flow_log_max_aggregation_interval               = 600
  vpc_flow_log_iam_role_name                      = "${var.name}-vpc-flow-log"
  vpc_flow_log_iam_role_use_name_prefix           = true
  vpc_flow_log_iam_policy_name                    = "${var.name}-vpc-flow-log"
  vpc_flow_log_iam_policy_use_name_prefix         = true
}

# Free S3 gateway endpoint: ECR image layers are served from S3, so pulls skip the NAT.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(module.vpc.private_route_table_ids, module.vpc.public_route_table_ids)

  tags = {
    Name = "${var.name}-s3"
  }
}
