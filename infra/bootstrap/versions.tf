terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.73.0"
    }
  }

  # Bootstrap starts with LOCAL state because the state bucket does not exist yet.
  # After the first `terraform apply`, uncomment this block, replace ACCOUNT_ID and run:
  #   terraform init -migrate-state
  # (see infra/README.md, "Bootstrap").
  #
  # backend "s3" {
  #   bucket       = "pulse-tfstate-ACCOUNT_ID"
  #   key          = "bootstrap/terraform.tfstate"
  #   region       = "us-east-2"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "pulse"
      Environment = "shared"
      ManagedBy   = "terraform"
      Owner       = var.owner
    }
  }
}
