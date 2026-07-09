# --------------------------------------------------------------------------
# App Runner: the two long-running services. Managed (public) egress — they
# reach Aurora on its public endpoint (see network.tf) and the internet for
# the LLM API / Google JWKS. auto_deployments: pushing :latest to ECR rolls
# a new deployment with no further step.
# --------------------------------------------------------------------------

# Access role — lets App Runner pull the images from ECR.
data "aws_iam_policy_document" "apprunner_build_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_access" {
  name               = "${local.name}-apprunner-access"
  assume_role_policy = data.aws_iam_policy_document.apprunner_build_assume.json
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# Instance role — lets the running services read their secrets.
data "aws_iam_policy_document" "apprunner_tasks_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_instance" {
  name               = "${local.name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.apprunner_tasks_assume.json
}

data "aws_iam_policy_document" "apprunner_secrets" {
  statement {
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.backend_db_url.arn,
      aws_secretsmanager_secret.agent_db_url.arn,
      aws_secretsmanager_secret.admin_ro_db_url.arn,
      aws_secretsmanager_secret.jwt.arn,
      aws_secretsmanager_secret.llm_api_key.arn,
      aws_secretsmanager_secret.agent_shared_token.arn,
    ]
  }
}

resource "aws_iam_role_policy" "apprunner_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.apprunner_instance.id
  policy = data.aws_iam_policy_document.apprunner_secrets.json
}

# Cost control: exactly one instance per service (scale-out is a later knob).
resource "aws_apprunner_auto_scaling_configuration_version" "single" {
  auto_scaling_configuration_name = "${local.name}-single"
  min_size                        = 1
  max_size                        = 1
  max_concurrency                 = 100
}

locals {
  registry     = "${local.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"
  frontend_url = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

# ---- data-agent (created first: the backend needs its URL) ----------------
resource "aws_apprunner_service" "data_agent" {
  service_name                   = "${local.name}-data-agent"
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.single.arn

  source_configuration {
    auto_deployments_enabled = true
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }
    image_repository {
      image_repository_type = "ECR"
      image_identifier      = "${local.registry}/data-qa/data-agent:${var.image_tag}"
      image_configuration {
        port = "8100"
        runtime_environment_variables = {
          APP_ENV         = "prod"
          DB_SSL          = "require"
          LLM_PROVIDER    = var.llm_provider
          SANDBOX_RUNTIME = "pyodide"
        }
        # The one hand-set secret (data-qa/llm-api-key) feeds the selected provider.
        runtime_environment_secrets = merge(
          {
            AGENT_DATABASE_URL    = aws_secretsmanager_secret.agent_db_url.arn
            ADMIN_RO_DATABASE_URL = aws_secretsmanager_secret.admin_ro_db_url.arn
            AGENT_SHARED_TOKEN    = aws_secretsmanager_secret.agent_shared_token.arn
          },
          var.llm_provider == "deepseek" ? { DEEPSEEK_API_KEY = aws_secretsmanager_secret.llm_api_key.arn } : {},
          var.llm_provider == "anthropic" ? { ANTHROPIC_API_KEY = aws_secretsmanager_secret.llm_api_key.arn } : {},
        )
      }
    }
  }

  instance_configuration {
    cpu               = var.agent_cpu
    memory            = var.agent_memory
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  health_check_configuration {
    protocol = "HTTP"
    path     = "/health"
  }

  tags = { Name = "${local.name}-data-agent" }
}

# ---- backend-api (public entrypoint) ---------------------------------------
resource "aws_apprunner_service" "backend_api" {
  service_name                   = "${local.name}-backend-api"
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.single.arn

  source_configuration {
    auto_deployments_enabled = true
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }
    image_repository {
      image_repository_type = "ECR"
      image_identifier      = "${local.registry}/data-qa/backend-api:${var.image_tag}"
      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          APP_ENV            = "prod"
          AUTH_MODE          = "google"
          GOOGLE_CLIENT_ID   = var.google_client_id
          ADMIN_EMAILS       = var.admin_emails
          DB_SSL             = "require"
          AGENT_URL          = "https://${aws_apprunner_service.data_agent.service_url}"
          EXTRA_CORS_ORIGINS = local.frontend_url
        }
        runtime_environment_secrets = {
          DATABASE_URL       = aws_secretsmanager_secret.backend_db_url.arn
          JWT_SECRET         = aws_secretsmanager_secret.jwt.arn
          AGENT_SHARED_TOKEN = aws_secretsmanager_secret.agent_shared_token.arn
        }
      }
    }
  }

  instance_configuration {
    cpu               = var.backend_cpu
    memory            = var.backend_memory
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  health_check_configuration {
    protocol = "HTTP"
    path     = "/health"
  }

  tags = { Name = "${local.name}-backend-api" }
}
