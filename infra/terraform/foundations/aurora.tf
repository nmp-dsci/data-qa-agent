# --------------------------------------------------------------------------
# Aurora Serverless v2 (PostgreSQL 16). Scale-to-zero when idle (db_min_acu=0)
# — the s12 choice for a personal test app. pgvector is enabled at the SQL
# level later (CREATE EXTENSION vector), same as the local pgvector container.
# --------------------------------------------------------------------------
resource "aws_db_subnet_group" "aurora" {
  name       = "${local.name}-aurora"
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = "${local.name}-aurora" }
}

resource "aws_rds_cluster_parameter_group" "aurora" {
  name        = "${local.name}-aurora-pg16"
  family      = "aurora-postgresql16"
  description = "data-qa Aurora PG16 cluster params"

  # Enforce TLS — the app connects with DB_SSL=require.
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_rds_cluster" "main" {
  cluster_identifier = "${local.name}-aurora"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned" # Serverless v2 runs under provisioned mode
  engine_version     = var.db_engine_version

  database_name   = var.db_name
  master_username = var.db_master_username
  master_password = random_password.db_master.result
  port            = 5432

  db_subnet_group_name            = aws_db_subnet_group.aurora.name
  vpc_security_group_ids          = [aws_security_group.aurora.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.aurora.name

  storage_encrypted = true

  serverlessv2_scaling_configuration {
    min_capacity = var.db_min_acu # 0 = scale-to-zero / auto-pause
    max_capacity = var.db_max_acu
  }

  # Dev posture — easy to tear down and rebuild from Alembic migrations.
  skip_final_snapshot = true
  deletion_protection = false
  apply_immediately   = true

  tags = { Name = "${local.name}-aurora" }
}

resource "aws_rds_cluster_instance" "writer" {
  identifier         = "${local.name}-aurora-1"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version

  # Public endpoint — the consequence of the no-NAT decision (see network.tf).
  # App Runner (managed egress) and local tooling reach the DB here; TLS is
  # forced at the cluster parameter group and all passwords are random 32-char.
  publicly_accessible = true

  tags = { Name = "${local.name}-aurora-writer" }
}
