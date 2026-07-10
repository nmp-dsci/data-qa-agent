output "vpc_id" {
  description = "Project VPC id."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnet ids (Aurora subnet group + Phase-D App Runner VPC connector)."
  value       = aws_subnet.private[*].id
}

output "aurora_endpoint" {
  description = "Aurora writer endpoint (host for DATABASE_URL)."
  value       = aws_rds_cluster.main.endpoint
}

output "aurora_reader_endpoint" {
  description = "Aurora reader endpoint."
  value       = aws_rds_cluster.main.reader_endpoint
}

output "db_secret_arn" {
  description = "Secrets Manager ARN for the DB connection bundle."
  value       = aws_secretsmanager_secret.db.arn
}

output "jwt_secret_arn" {
  description = "Secrets Manager ARN for JWT_SECRET."
  value       = aws_secretsmanager_secret.jwt.arn
}

output "llm_api_key_secret_arn" {
  description = "Secrets Manager ARN for the LLM API key (set its value via CLI in Phase D)."
  value       = aws_secretsmanager_secret.llm_api_key.arn
}

output "data_bucket" {
  description = "S3 bucket for the source CSVs."
  value       = aws_s3_bucket.source_data.id
}

output "frontend_bucket" {
  description = "S3 bucket the built frontend is uploaded to."
  value       = aws_s3_bucket.frontend.id
}

output "cloudfront_domain" {
  description = "Public HTTPS URL of the app (default CloudFront domain)."
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id (for cache invalidations on deploy)."
  value       = aws_cloudfront_distribution.frontend.id
}

output "ecr_repository_urls" {
  description = "ECR repository URLs, keyed by service."
  value       = { for k, r in aws_ecr_repository.service : k => r.repository_url }
}

output "backend_api_url" {
  description = "Public HTTPS URL of backend-api (App Runner) — the frontend's VITE_API_URL."
  value       = "https://${aws_apprunner_service.backend_api.service_url}"
}

output "data_agent_url" {
  description = "URL of the data-agent (App Runner; requires X-Agent-Token except /health)."
  value       = "https://${aws_apprunner_service.data_agent.service_url}"
}
