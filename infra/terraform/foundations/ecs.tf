# --------------------------------------------------------------------------
# One-shot jobs (Fargate run-task): db-migrate (Alembic — also creates the
# vector/pgcrypto extensions and rotates role passwords) and data-pipeline
# (dlt ingest from S3 + dbt build). They run in the app subnets with public
# IPs so they can pull from ECR / reach S3 without NAT. $0 when not running.
# Launch with scripts/run_job.sh.
# --------------------------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled" # cost: plain CloudWatch logs are enough for one-shots
  }
}

resource "aws_cloudwatch_log_group" "migrate" {
  name              = "/ecs/${local.name}-migrate"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/ecs/${local.name}-pipeline"
  retention_in_days = 30
}

# ---- Execution role (image pull, logs, secrets injection) ------------------
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ecs_execution_secrets" {
  statement {
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.admin_db_url.arn,
      aws_secretsmanager_secret.db.arn,
    ]
  }
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name   = "read-job-secrets"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution_secrets.json
}

# ---- Task role for the pipeline (reads the source CSVs from S3) -----------
resource "aws_iam_role" "pipeline_task" {
  name               = "${local.name}-pipeline-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "pipeline_s3" {
  statement {
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.source_data.arn, "${aws_s3_bucket.source_data.arn}/*"]
  }
}

resource "aws_iam_role_policy" "pipeline_s3" {
  name   = "read-source-data"
  role   = aws_iam_role.pipeline_task.id
  policy = data.aws_iam_policy_document.pipeline_s3.json
}

# ---- Task definitions -------------------------------------------------------
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "migrate"
    image     = "${local.registry}/data-qa/db-migrate:${var.image_tag}"
    essential = true
    secrets = [
      { name = "ADMIN_DATABASE_URL", valueFrom = aws_secretsmanager_secret.admin_db_url.arn },
      # Rotate the baked-in role passwords to the generated ones (ECS supports
      # JSON-key extraction from the db bundle; App Runner does not).
      { name = "APP_USER_PW", valueFrom = "${aws_secretsmanager_secret.db.arn}:app_password::" },
      { name = "AGENT_RO_PW", valueFrom = "${aws_secretsmanager_secret.db.arn}:agent_password::" },
      { name = "ADMIN_RO_PW", valueFrom = "${aws_secretsmanager_secret.db.arn}:admin_ro_password::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.migrate.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "migrate"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "pipeline" {
  family                   = "${local.name}-pipeline"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "4096" # ~3M rows; dlt buffers + dbt build headroom
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.pipeline_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "pipeline"
    image     = "${local.registry}/data-qa/data-pipeline:${var.image_tag}"
    essential = true
    environment = [
      { name = "PIPELINE_SOURCE", value = "full" },
      { name = "DATA_S3_BUCKET", value = aws_s3_bucket.source_data.id },
      { name = "PGSSLMODE", value = "require" }, # dbt: never fall back to plaintext
    ]
    secrets = [
      { name = "ADMIN_DATABASE_URL", valueFrom = aws_secretsmanager_secret.admin_db_url.arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.pipeline.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "pipeline"
      }
    }
  }])
}
