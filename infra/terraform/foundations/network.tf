# --------------------------------------------------------------------------
# Minimal VPC, no NAT (locked in s12 for idle cost). Consequence (Phase D):
# App Runner egress is all-or-nothing — both services need the internet (LLM
# API, Google JWKS, backend→agent over its public URL), so they keep MANAGED
# egress and reach Aurora over its *public* endpoint instead of a VPC
# connector. The subnets therefore route to an IGW, Aurora is publicly
# resolvable (TLS forced + long random passwords), and the one-shot ECS jobs
# run here with public IPs (pull ECR / reach S3 without NAT or VPC endpoints).
# Phase F hardening: private agent + restricted DB ingress.
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

# Security group for Aurora. Public 5432 is the accepted v1 trade-off of the
# no-NAT decision: App Runner egress IPs aren't stable/publishable, so the DB
# relies on rds.force_ssl=1 + 32-char random passwords. Tighten in Phase F.
resource "aws_security_group" "aurora" {
  name        = "${local.name}-aurora"
  description = "Postgres access to Aurora."
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "PostgreSQL (TLS forced; strong creds; Phase F restricts)"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
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
