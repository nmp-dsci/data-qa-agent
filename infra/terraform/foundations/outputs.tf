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

output "ecr_repository_urls" {
  description = "ECR repository URLs, keyed by service."
  value       = { for k, r in aws_ecr_repository.service : k => r.repository_url }
}
