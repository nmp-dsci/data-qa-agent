# The demo stack (s52): one ECR repo, one App Runner service, the existing
# S3 + CloudFront frontend, two alarms. No VPC, no database, no Secrets
# Manager — the backend runs DEMO_MODE=1 DB_DISABLED=1, replays the baked-in
# chat pack, and the exhibit tabs read a static JSON dump shipped with the
# frontend. Same shape as transcript-rag-agent/infra/terraform/demo.
#
# Cutover from ../foundations (the Aurora-backed stack this replaces): the
# frontend bucket, CloudFront distribution and alert topics are IMPORTED here
# so the public URL and the alarm email survive; ../foundations carries the
# matching `removed` blocks so destroying it leaves them alone.
#
# First-time order (locally, then let CI take over):
#
#   terraform init
#   terraform apply -target=aws_ecr_repository.demo     # repo before first push
#   IMAGE_REPO=data-qa/demo ../../../scripts/aws_build_push.sh   # image before service
#   terraform apply                                     # imports + service

terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  backend "s3" {
    bucket       = "data-qa-tfstate-089783391188"
    key          = "demo/terraform.tfstate"
    region       = "ap-southeast-2"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile != "" ? var.aws_profile : null
  default_tags {
    tags = { Project = var.project, ManagedBy = "terraform", Ticket = "s52", Module = "demo" }
  }
}

# Billing metrics only exist in us-east-1.
provider "aws" {
  alias   = "use1"
  region  = "us-east-1"
  profile = var.aws_profile != "" ? var.aws_profile : null
  default_tags {
    tags = { Project = var.project, ManagedBy = "terraform", Ticket = "s52", Module = "demo" }
  }
}

variable "aws_region" {
  type    = string
  default = "ap-southeast-2"
}

variable "aws_profile" {
  description = "Local AWS profile; empty in CI (OIDC env credentials)."
  type        = string
  default     = ""
}

variable "project" {
  type    = string
  default = "data-qa"
}

variable "image_tag" {
  description = "Tag the service runs. CI pushes :latest then calls start-deployment."
  type        = string
  default     = "latest"
}

variable "alert_email" {
  type    = string
  default = "nathanphillips369@gmail.com"
}

variable "billing_alarm_usd" {
  description = "Alarm when the month-to-date AWS bill crosses this many USD (whole account)."
  type        = number
  default     = 30
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  name       = var.project
}

# ── Image registry ───────────────────────────────────────────────────────

resource "aws_ecr_repository" "demo" {
  name                 = "${local.name}/demo"
  image_tag_mutability = "MUTABLE" # :latest is the deploy pointer

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "demo" {
  repository = aws_ecr_repository.demo.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the 5 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# ── App Runner ───────────────────────────────────────────────────────────

data "aws_iam_policy_document" "apprunner_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_ecr_access" {
  name               = "${local.name}-demo-apprunner-ecr-access"
  assume_role_policy = data.aws_iam_policy_document.apprunner_trust.json
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr_access" {
  role       = aws_iam_role.apprunner_ecr_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# Exactly one instance: the demo's per-IP rate limits and concurrency cap are
# in-process, so they only hold on a single replica.
resource "aws_apprunner_auto_scaling_configuration_version" "single" {
  auto_scaling_configuration_name = "${local.name}-demo-single"
  min_size                        = 1
  max_size                        = 1
  max_concurrency                 = 100
}

resource "aws_apprunner_service" "demo" {
  service_name                   = "${local.name}-demo"
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.single.arn

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr_access.arn
    }
    # $1/month per service for the ECR-push trigger; the deploy workflow calls
    # start-deployment itself instead, which also makes the release step explicit.
    auto_deployments_enabled = false
    image_repository {
      image_identifier      = "${aws_ecr_repository.demo.repository_url}:${var.image_tag}"
      image_repository_type = "ECR"
      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          APP_ENV     = "prod"
          DEMO_MODE   = "1"
          DB_DISABLED = "1"
          # google with no client id: /auth/config advertises the demo door and
          # nothing else; dev-login stays 403. No GOOGLE_CLIENT_ID means no
          # owner door — there is no users table for it to provision into.
          AUTH_MODE = "google"
          # No AGENT_URL on purpose: demo mode never dials the agent (the
          # agent_client choke point 501s first), and App Runner discards an
          # empty value, so declaring "" made every apply an UpdateService.
          EXTRA_CORS_ORIGINS = "https://${aws_cloudfront_distribution.frontend.domain_name}"
        }
        # No secrets on purpose. JWT_SECRET is generated at process start when
        # DB_DISABLED is set (see backend config) — sessions belong to a constant
        # visitor and last at most one deploy.
      }
    }
  }

  instance_configuration {
    cpu    = "256"
    memory = "512"
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  observability_configuration {
    observability_enabled = false
  }

  tags = { Name = "${local.name}-demo" }
}

# ── Frontend: private S3 bucket behind CloudFront (imported from foundations) ──

import {
  to = aws_s3_bucket.frontend
  id = "data-qa-frontend-089783391188"
}

import {
  to = aws_s3_bucket_public_access_block.frontend
  id = "data-qa-frontend-089783391188"
}

import {
  to = aws_cloudfront_origin_access_control.frontend
  id = "E17OAQ37ZTNJBY"
}

import {
  to = aws_cloudfront_distribution.frontend
  id = "ERWBDUR0GS061"
}

import {
  to = aws_s3_bucket_policy.frontend
  id = "data-qa-frontend-089783391188"
}

resource "aws_s3_bucket" "frontend" {
  bucket = "${local.name}-frontend-${local.account_id}"
  tags   = { Name = "${local.name}-frontend" }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.name}-frontend-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "${local.name} frontend"
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
  }

  # SPA fallback — client-side routes resolve to index.html.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  tags = { Name = "${local.name}-frontend" }
}

data "aws_iam_policy_document" "frontend_bucket" {
  statement {
    sid       = "AllowCloudFrontOAC"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.frontend.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_bucket.json
}

# ── Alarms (topics + subscriptions imported from foundations) ────────────

import {
  to = aws_sns_topic.alerts
  id = "arn:aws:sns:ap-southeast-2:089783391188:data-qa-alerts"
}

import {
  to = aws_sns_topic_subscription.alerts_email
  id = "arn:aws:sns:ap-southeast-2:089783391188:data-qa-alerts:7ac96912-8758-4057-9395-911510ef82ed"
}

import {
  to = aws_sns_topic.alerts_use1
  id = "arn:aws:sns:us-east-1:089783391188:data-qa-alerts"
}

import {
  to = aws_sns_topic_subscription.alerts_email_use1
  id = "arn:aws:sns:us-east-1:089783391188:data-qa-alerts:1d9c9b5b-4d5b-4733-8507-984161d7d130"
}

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sns_topic" "alerts_use1" {
  provider = aws.use1
  name     = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email_use1" {
  provider  = aws.use1
  topic_arn = aws_sns_topic.alerts_use1.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Sustained 5xx means the demo is broken for whoever just opened the portfolio.
resource "aws_cloudwatch_metric_alarm" "demo_5xx" {
  alarm_name        = "${local.name}-demo-5xx"
  alarm_description = "data-qa demo returned 5 or more 5xx responses in 5 minutes."
  namespace         = "AWS/AppRunner"
  metric_name       = "5xxStatusResponses"
  dimensions = {
    ServiceName = aws_apprunner_service.demo.service_name
    ServiceID   = aws_apprunner_service.demo.service_id
  }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Whole-account bill guard. The threshold drops with the stack: ≈$25/mo is the
# expected run-rate for all four demo sites once Aurora is gone.
resource "aws_cloudwatch_metric_alarm" "billing" {
  provider            = aws.use1
  alarm_name          = "${local.name}-billing-over-${var.billing_alarm_usd}usd"
  alarm_description   = "Estimated month-to-date AWS charges crossed ${var.billing_alarm_usd} USD."
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  dimensions          = { Currency = "USD" }
  statistic           = "Maximum"
  period              = 21600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.billing_alarm_usd
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts_use1.arn]
}

# ── Outputs (the deploy workflow reads these) ────────────────────────────

output "backend_api_url" {
  value = "https://${aws_apprunner_service.demo.service_url}"
}

output "apprunner_service_arn" {
  value = aws_apprunner_service.demo.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.demo.repository_url
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.id
}

output "cloudfront_domain" {
  value = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.frontend.id
}
