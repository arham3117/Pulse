terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.73.0"
    }
    # Used inside terraform-aws-modules/eks; pinned here so upgrades are deliberate.
    tls = {
      source  = "hashicorp/tls"
      version = "4.4.2"
    }
    time = {
      source  = "hashicorp/time"
      version = "0.14.2"
    }
    cloudinit = {
      source  = "hashicorp/cloudinit"
      version = "2.4.1"
    }
    null = {
      source  = "hashicorp/null"
      version = "3.3.2"
    }
  }
}
