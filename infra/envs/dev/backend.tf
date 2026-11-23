terraform {
  # Replace ACCOUNT_ID with your 12-digit account id (bootstrap output state_bucket_name).
  backend "s3" {
    bucket       = "pulse-tfstate-ACCOUNT_ID"
    key          = "envs/dev/terraform.tfstate"
    region       = "us-east-2"
    encrypt      = true
    use_lockfile = true # S3-native state locking; no DynamoDB table needed
  }
}
