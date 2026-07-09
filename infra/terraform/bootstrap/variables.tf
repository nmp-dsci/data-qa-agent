variable "aws_region" {
  description = "AWS region for all data-qa-agent infrastructure."
  type        = string
  default     = "ap-southeast-2" # Sydney — locked in s12
}

variable "aws_profile" {
  description = "Local AWS CLI profile used to run the bootstrap. In CI this is unset (OIDC provides creds)."
  type        = string
  default     = "data-qa"
}

variable "project" {
  description = "Short project slug used as a prefix for resource names."
  type        = string
  default     = "data-qa"
}

variable "github_repo" {
  description = "GitHub repo (owner/name) allowed to assume the CI deploy role via OIDC."
  type        = string
  default     = "nmp-dsci/data-qa-agent"
}
