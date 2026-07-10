# --------------------------------------------------------------------------
# Minimal VPC, no NAT (locked in s12 for idle cost). Consequence (Phase D):
# App Runner egress is all-or-nothing — both services need the internet (LLM
# API, Google JWKS, backend→agent over its public URL), so they keep MANAGED
# egress and reach Aurora over its *public* endpoint instead of a VPC
# connector. The subnets therefore route to an IGW, Aurora is publicly
# resolvable (TLS forced + long random passwords; 5432 ingress restricted to
# the jobs SG + regional EC2 ranges + operator CIDRs — see the aurora SGs
# below), and the one-shot ECS jobs run here with public IPs (pull ECR / reach
# S3 without NAT or VPC endpoints). Phase F hardening: private agent.
# --------------------------------------------------------------------------
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.name}-vpc" }
}

# NOTE: resource label kept as "private" to avoid destroying/recreating the
# subnets (and the Aurora cluster on them); the IGW route below makes them
# effectively public.
resource "aws_subnet" "private" {
  count                   = length(local.azs)
  vpc_id                  = aws_vpc.main.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  map_public_ip_on_launch = true

  tags = {
    Name = "${local.name}-app-${local.azs[count.index]}"
    Tier = "app"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_route_table" "app" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-app" }
}

resource "aws_route_table_association" "app" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.app.id
}

# Security group for Aurora. The endpoint stays public (the no-NAT decision:
# App Runner keeps MANAGED egress for the LLM API, so it reaches the DB from
# AWS public IP space — a VPC connector would force ALL egress into the VPC and
# need a NAT gateway for the internet), but 5432 is no longer open to
# 0.0.0.0/0: in-VPC clients (the one-shot ECS jobs) get an SG-to-SG rule, App
# Runner is admitted via the region's published EC2 ranges (its managed-egress
# source pool — see aurora_apprunner below), and any operator IPs come from
# var.db_extra_ingress_cidrs. TLS stays forced + 32-char random passwords.
resource "aws_security_group" "aurora" {
  name = "${local.name}-aurora"
  # NOTE: description is force-new on SGs, and this SG can't be replaced while
  # the RDS ENI holds it — keep the original wording forever.
  description = "Postgres access to Aurora from within the VPC."
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from the one-shot ECS jobs (migrate/pipeline)"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.jobs.id]
  }

  dynamic "ingress" {
    for_each = length(var.db_extra_ingress_cidrs) > 0 ? [1] : []
    content {
      description = "PostgreSQL from operator CIDRs (local tooling)"
      from_port   = 5432
      to_port     = 5432
      protocol    = "tcp"
      cidr_blocks = var.db_extra_ingress_cidrs
    }
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-aurora" }
}

# App Runner managed egress comes from AWS-owned public IPs in this region's
# EC2 ranges (not stable per-service, but bounded by ip-ranges.json). Admitting
# those ranges is the tightest NAT-free ingress for the two services. The list
# (~61 CIDRs for ap-southeast-2) exceeds the 60-rules-per-SG default quota, so
# it is chunked across extra SGs attached to the cluster alongside the main
# one. Rule contents drift as AWS republishes ip-ranges.json — expect benign
# in-place diffs on later plans.
data "aws_ip_ranges" "regional_ec2" {
  regions  = [var.aws_region]
  services = ["ec2"]
}

locals {
  apprunner_egress_chunks = chunklist(sort(data.aws_ip_ranges.regional_ec2.cidr_blocks), 50)
}

resource "aws_security_group" "aurora_apprunner" {
  count       = length(local.apprunner_egress_chunks)
  name        = "${local.name}-aurora-apprunner-${count.index}"
  description = "Postgres from regional AWS EC2 ranges (App Runner managed egress)."
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "PostgreSQL from AWS ${var.aws_region} EC2 ranges (App Runner egress)"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = local.apprunner_egress_chunks[count.index]
  }

  tags = { Name = "${local.name}-aurora-apprunner-${count.index}" }
}

# One-shot ECS jobs (migrate / pipeline): egress only.
resource "aws_security_group" "jobs" {
  name        = "${local.name}-jobs"
  description = "Egress-only SG for the one-shot ECS tasks."
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-jobs" }
}
