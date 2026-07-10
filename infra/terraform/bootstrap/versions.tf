terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Bootstrap intentionally uses LOCAL state: it creates the very S3 bucket +
  # DynamoDB table that every other module then uses as a remote backend.
  # Chicken-and-egg — this one module cannot store its state remotely.
}
