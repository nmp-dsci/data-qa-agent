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

variable "deploy_role_scoped" {
  description = <<-EOT
    Whether the CI deploy role uses the least-privilege policy (s32 W4, decision
    Q6) instead of AdministratorAccess.

    Default true — the scoped policy is the intended state. Flip to false and
    apply to revert if a deploy ever fails on a missing permission, then add the
    action to the policy rather than leaving the role broad.
  EOT
  type        = bool
  default     = true
}
