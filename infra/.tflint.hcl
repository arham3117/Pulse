# tflint configuration for every root and module under infra/.
# Usage (from repo root):
#   tflint --init --config "$PWD/infra/.tflint.hcl"
#   tflint --recursive --chdir infra --config "$PWD/infra/.tflint.hcl"

config {
  # Inspect calls to local modules (infra/modules/*) with the caller's values.
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  version = "0.50.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}
