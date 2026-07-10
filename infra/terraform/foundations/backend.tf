terraform {
  # Remote state lives in the bucket created by ../bootstrap.
  # Bucket name = "<project>-tfstate-<account_id>" = data-qa-tfstate-089783391188.
  # If the account ever changes, update this (or pass via -backend-config).
  # The backend needs its own creds (it doesn't read the provider block).
  # Locally: `export AWS_PROFILE=data-qa` before terraform commands.
  # CI: GitHub-OIDC env credentials are picked up automatically.
  backend "s3" {
    bucket  = "data-qa-tfstate-089783391188"
    key     = "foundations/terraform.tfstate"
    region  = "ap-southeast-2"
    encrypt = true

    # S3-native state locking (replaces the deprecated DynamoDB lock table).
    use_lockfile = true
  }
}
