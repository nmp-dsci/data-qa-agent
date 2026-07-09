# --------------------------------------------------------------------------
# Secrets Manager. Terraform GENERATES the DB + JWT secrets (no human ever
# handles them). The LLM API key is the ONE value you set by hand later
# (Phase D) via `aws secretsmanager put-secret-value` — so its version here
# is a placeholder with ignore_changes, and your CLI write is never clobbered.
# --------------------------------------------------------------------------

resource "random_password" "db_master" {
  length  = 32
  special = true
  # Keep it URL/DSN-safe.
  override_special = "!#$%*-_=+"
}

resource "random_password" "app_db" {
  length           = 32
  special          = true
  override_special = "!#$%*-_=+"
}

resource "random_password" "agent_db" {
  length           = 32
  special          = true
  override_special = "!#$%*-_=+"
}

resource "random_password" "admin_ro_db" {
  length           = 32
  special          = true
  override_special = "!#$%*-_=+"
}

resource "random_password" "jwt" {
  length  = 48
  special = false
}

# Backend -> agent shared token (the agent's public URL rejects other callers).
resource "random_password" "agent_shared_token" {
  length  = 48
  special = false
}

locals {
  db_host = aws_rds_cluster.main.endpoint
  db_tail = "${local.db_host}:5432/${var.db_name}"
  # Passwords can contain URL-special chars — percent-encode them in DSNs.
  url_admin    = "postgresql://${var.db_master_username}:${urlencode(random_password.db_master.result)}@${local.db_tail}"
  url_app      = "postgresql+asyncpg://app_user:${urlencode(random_password.app_db.result)}@${local.db_tail}"
  url_agent    = "postgresql+asyncpg://agent_ro:${urlencode(random_password.agent_db.result)}@${local.db_tail}"
  url_admin_ro = "postgresql+asyncpg://admin_ro:${urlencode(random_password.admin_ro_db.result)}@${local.db_tail}"
}

# ---- Database connection bundle ------------------------------------------
resource "aws_secretsmanager_secret" "db" {
  name        = "${local.name}/db"
  description = "Aurora connection details for data-qa (master + app_user)."
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    master_username   = var.db_master_username
    master_password   = random_password.db_master.result
    app_username      = "app_user"
    app_password      = random_password.app_db.result
    agent_username    = "agent_ro"
    agent_password    = random_password.agent_db.result
    admin_ro_username = "admin_ro"
    admin_ro_password = random_password.admin_ro_db.result
    host              = aws_rds_cluster.main.endpoint
    reader_host       = aws_rds_cluster.main.reader_endpoint
    port              = 5432
    dbname            = var.db_name
  })
}

# ---- Ready-made connection URLs -------------------------------------------
# App Runner injects whole secret values only (no JSON-key extraction, unlike
# ECS), so each service's URL is its own secret. ECS jobs use these too.
resource "aws_secretsmanager_secret" "admin_db_url" {
  name        = "${local.name}/admin-database-url"
  description = "ADMIN_DATABASE_URL (master) for the migrate/pipeline jobs."
}

resource "aws_secretsmanager_secret_version" "admin_db_url" {
  secret_id     = aws_secretsmanager_secret.admin_db_url.id
  secret_string = local.url_admin
}

resource "aws_secretsmanager_secret" "backend_db_url" {
  name        = "${local.name}/backend-database-url"
  description = "DATABASE_URL (app_user, asyncpg) for backend-api."
}

resource "aws_secretsmanager_secret_version" "backend_db_url" {
  secret_id     = aws_secretsmanager_secret.backend_db_url.id
  secret_string = local.url_app
}

resource "aws_secretsmanager_secret" "agent_db_url" {
  name        = "${local.name}/agent-database-url"
  description = "AGENT_DATABASE_URL (agent_ro, asyncpg) for data-agent."
}

resource "aws_secretsmanager_secret_version" "agent_db_url" {
  secret_id     = aws_secretsmanager_secret.agent_db_url.id
  secret_string = local.url_agent
}

resource "aws_secretsmanager_secret" "admin_ro_db_url" {
  name        = "${local.name}/admin-ro-database-url"
  description = "ADMIN_RO_DATABASE_URL (admin_ro, asyncpg) for data-agent."
}

resource "aws_secretsmanager_secret_version" "admin_ro_db_url" {
  secret_id     = aws_secretsmanager_secret.admin_ro_db_url.id
  secret_string = local.url_admin_ro
}

resource "aws_secretsmanager_secret" "agent_shared_token" {
  name        = "${local.name}/agent-shared-token"
  description = "X-Agent-Token shared between backend-api and data-agent."
}

resource "aws_secretsmanager_secret_version" "agent_shared_token" {
  secret_id     = aws_secretsmanager_secret.agent_shared_token.id
  secret_string = random_password.agent_shared_token.result
}

# ---- App JWT signing secret ----------------------------------------------
resource "aws_secretsmanager_secret" "jwt" {
  name        = "${local.name}/jwt-secret"
  description = "JWT_SECRET for data-qa (app-issued tokens; Google auth uses JWKS)."
}

resource "aws_secretsmanager_secret_version" "jwt" {
  secret_id     = aws_secretsmanager_secret.jwt.id
  secret_string = random_password.jwt.result
}

# ---- LLM API key (YOU set this, Phase D) ---------------------------------
resource "aws_secretsmanager_secret" "llm_api_key" {
  name        = "${local.name}/llm-api-key"
  description = "LLM provider API key (DeepSeek/Anthropic). Set via CLI, not Terraform."
}

resource "aws_secretsmanager_secret_version" "llm_api_key" {
  secret_id     = aws_secretsmanager_secret.llm_api_key.id
  secret_string = "REPLACE_ME_VIA_CLI"

  # Your `aws secretsmanager put-secret-value` write is the source of truth —
  # Terraform must not overwrite it on subsequent applies.
  lifecycle {
    ignore_changes = [secret_string]
  }
}
