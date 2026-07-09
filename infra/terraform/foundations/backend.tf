terraform {
  # Remote state lives in the bucket created by ../bootstrap.
  # Bucket name = "<project>-tfstate-<account_id>" = data-qa-tfstate-089783391188.
  # If the account ever changes, update this (or pass via -backend-config).
  backend "s3" {
    bucket         = "data-qa-tfstate-089783391188"
    key            = "foundations/terraform.tfstate"
    region         = "ap-southeast-2"
    dynamodb_table = "data-qa-tf-lock"
    encrypt        = true
  }
}
