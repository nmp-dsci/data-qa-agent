output "state_bucket" {
  description = "S3 bucket holding remote Terraform state. Wire this into foundations/backend.tf."
  value       = aws_s3_bucket.tfstate.id
}

output "lock_table" {
  description = "DynamoDB table used for state locking."
  value       = aws_dynamodb_table.tf_lock.name
}

output "github_deploy_role_arn" {
  description = "IAM role ARN for GitHub Actions OIDC (used in Phase E deploy-aws.yml)."
  value       = aws_iam_role.github_deploy.arn
}

output "oidc_provider_arn" {
  description = "GitHub Actions OIDC provider ARN."
  value       = aws_iam_openid_connect_provider.github.arn
}
