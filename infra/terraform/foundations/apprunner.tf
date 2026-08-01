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
      # s32: tracing export + the ops ingest token.
      aws_secretsmanager_secret.logfire_token.arn,
      aws_secretsmanager_secret.ops_ingest_token.arn,
    ]
  }
}

resource "aws_iam_role_policy" "apprunner_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.apprunner_instance.id
  policy = data.aws_iam_policy_document.apprunner_secrets.json
}

# ---- Tier-2 ops saturation: read-only CloudWatch (s32 W2) ------------------
# The one AWS API the app itself calls, once per rollup refresh and never on a
# request path. GetMetricData takes no resource ARNs (the API is account-wide), so
# the grant is scoped by ACTION instead: read metrics, nothing else. Attached only
# when var.ops_cloudwatch_enabled is on, so the default deployment grants nothing
# extra and the deck renders Tier-1 telemetry alone.
data "aws_iam_policy_document" "apprunner_cloudwatch_read" {
  statement {
    effect = "Allow"
    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:ListMetrics",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "apprunner_cloudwatch_read" {
  count  = var.ops_cloudwatch_enabled ? 1 : 0
  name   = "read-cloudwatch-metrics"
  role   = aws_iam_role.apprunner_instance.id
  policy = data.aws_iam_policy_document.apprunner_cloudwatch_read.json
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

  # The service reads its secrets at instance launch — the *values* must exist,
  # not just the secret containers (referencing only the ARNs let Terraform
  # create the service before the versions were written → CREATE_FAILED).
  depends_on = [
    aws_secretsmanager_secret_version.agent_db_url,
    aws_secretsmanager_secret_version.admin_ro_db_url,
    aws_secretsmanager_secret_version.agent_shared_token,
    aws_secretsmanager_secret_version.llm_api_key,
    aws_secretsmanager_secret_version.logfire_token,
  ]

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
            # s32 W2: prod stopped running blind. Until the value is set by hand
            # the placeholder just means local-only tracing, not a failure.
            LOGFIRE_TOKEN = aws_secretsmanager_secret.logfire_token.arn
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

  depends_on = [
    aws_secretsmanager_secret_version.backend_db_url,
    aws_secretsmanager_secret_version.admin_ro_db_url,
    aws_secretsmanager_secret_version.jwt,
    aws_secretsmanager_secret_version.agent_shared_token,
    aws_secretsmanager_secret_version.logfire_token,
    aws_secretsmanager_secret_version.ops_ingest_token,
  ]

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
          # ---- Ops deck (s32) --------------------------------------------
          # Tier-2 saturation. Off unless ops_cloudwatch_enabled, in which case
          # the IAM read grant above is attached too — the flag and the grant
          # move together so the feature can never be on without permission.
          OPS_CLOUDWATCH_ENABLED         = var.ops_cloudwatch_enabled ? "1" : "0"
          OPS_CLOUDWATCH_REGION          = var.aws_region
          OPS_APPRUNNER_BACKEND_SERVICE  = "${local.name}-backend-api"
          OPS_APPRUNNER_AGENT_SERVICE    = aws_apprunner_service.data_agent.service_name
          OPS_APPRUNNER_MAX_CONCURRENCY  = tostring(aws_apprunner_auto_scaling_configuration_version.single.max_concurrency)
          OPS_AURORA_CLUSTER_ID          = aws_rds_cluster.main.cluster_identifier
          OPS_CLOUDFRONT_DISTRIBUTION_ID = aws_cloudfront_distribution.frontend.id
          OPS_MONTHLY_BUDGET_USD         = tostring(var.billing_alarm_usd)
        }
        runtime_environment_secrets = {
          DATABASE_URL       = aws_secretsmanager_secret.backend_db_url.arn
          JWT_SECRET         = aws_secretsmanager_secret.jwt.arn
          AGENT_SHARED_TOKEN = aws_secretsmanager_secret.agent_shared_token.arn
          # s32 W0: the ops rollup's cross-user read. SELECT-only + BYPASSRLS,
          # never reachable from a request handler.
          ADMIN_RO_DATABASE_URL = aws_secretsmanager_secret.admin_ro_db_url.arn
          # s32 W2/W0: traces out, operational outcomes in.
          LOGFIRE_TOKEN    = aws_secretsmanager_secret.logfire_token.arn
          OPS_INGEST_TOKEN = aws_secretsmanager_secret.ops_ingest_token.arn
          # s35: verifies X-Slack-Signature. Placeholder => the Slack endpoint
          # 404s, which is the correct posture for an env with no workspace.
          SLACK_SIGNING_SECRET = aws_secretsmanager_secret.slack_signing_secret.arn
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

# ---- mcp-server (s35 rung 3) -----------------------------------------------
# A third public service, and deliberately the least privileged of the three: it
# holds no database credentials at all. Its only capability is its dpk_ service
# key, so everything it can reach is whatever that key is granted — and the
# guardrails, RLS and audit trail it runs behind are backend-api's, not a second
# copy. ALLOWED_HOSTS must name this service's own hostname: the MCP SDK's
# DNS-rebinding guard matches the Host header including port, and refuses
# anything undeclared with a 421.
resource "aws_apprunner_service" "mcp_server" {
  service_name                   = "${local.name}-mcp-server"
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.single.arn

  depends_on = [aws_secretsmanager_secret_version.mcp_service_key]

  source_configuration {
    auto_deployments_enabled = true
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }
    image_repository {
      image_repository_type = "ECR"
      image_identifier      = "${local.registry}/data-qa/mcp-server:${var.image_tag}"
      image_configuration {
        port = "8200"
        runtime_environment_variables = {
          BACKEND_URL = "https://${aws_apprunner_service.backend_api.service_url}"
          # The SDK's DNS-rebinding guard matches the Host header exactly (or by
          # "host:*" prefix) — there is no suffix or allow-all form. App Runner
          # only assigns this service's hostname at create time, and a service
          # cannot reference its own service_url, so the value cannot be derived
          # here. Set var.mcp_allowed_hosts to the assigned hostname after the
          # first apply; until then the server answers only on localhost and
          # every remote request is a 421.
          ALLOWED_HOSTS = var.mcp_allowed_hosts
        }
        runtime_environment_secrets = {
          MCP_SERVICE_KEY = aws_secretsmanager_secret.mcp_service_key.arn
        }
      }
    }
  }

  instance_configuration {
    cpu    = var.backend_cpu
    memory = var.backend_memory
    # No instance role: this service talks to nothing but backend-api over HTTP.
  }

  tags = { Name = "${local.name}-mcp-server" }
}
