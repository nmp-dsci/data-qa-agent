provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile != "" ? var.aws_profile : null

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Ticket    = "s12"
      Module    = "foundations"
    }
  }
}

# Billing metrics only exist in us-east-1 (see alarms.tf).
provider "aws" {
  alias   = "use1"
  region  = "us-east-1"
  profile = var.aws_profile != "" ? var.aws_profile : null

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Ticket    = "s12"
      Module    = "foundations"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  name       = var.project
  # Two AZs — the minimum an Aurora DB subnet group requires.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}
