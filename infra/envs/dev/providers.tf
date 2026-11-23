provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "pulse"
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = var.owner
    }
  }
}
