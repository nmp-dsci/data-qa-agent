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

resource "random_password" "jwt" {
  length  = 48
  special = false
}

# ---- Database connection bundle ------------------------------------------
resource "aws_secretsmanager_secret" "db" {
  name        = "${local.name}/db"
  description = "Aurora connection details for data-qa (master + app_user)."
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    master_username = var.db_master_username
    master_password = random_password.db_master.result
    app_username    = "app_user"
    app_password    = random_password.app_db.result
    host            = aws_rds_cluster.main.endpoint
    reader_host     = aws_rds_cluster.main.reader_endpoint
    port            = 5432
    dbname          = var.db_name
  })
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
