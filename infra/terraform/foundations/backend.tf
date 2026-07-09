terraform {
  # Remote state lives in the bucket created by ../bootstrap.
  # Bucket name = "<project>-tfstate-<account_id>" = data-qa-tfstate-089783391188.
  # If the account ever changes, update this (or pass via -backend-config).
  backend "s3" {
    bucket  = "data-qa-tfstate-089783391188"
    key     = "foundations/terraform.tfstate"
    region  = "ap-southeast-2"
    profile = "data-qa" # backend init needs its own creds (doesn't read the provider block)
    encrypt = true

    # S3-native state locking (replaces the deprecated DynamoDB lock table).
    use_lockfile = true
  }
}
