# --------------------------------------------------------------------------
# Minimal VPC: two private subnets across two AZs for the Aurora DB subnet
# group. No NAT / IGW — Aurora needs no internet, and App Runner (Phase D)
# reaches the DB via a VPC connector on these subnets while using its own
# managed egress for the LLM. Keeping it NAT-free keeps idle cost at ~$0.
# --------------------------------------------------------------------------
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.name}-vpc" }
}

resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)

  tags = {
    Name = "${local.name}-private-${local.azs[count.index]}"
    Tier = "private"
  }
}

# Security group for Aurora: allow Postgres from inside the VPC only
# (the App Runner VPC connector lands here in Phase D).
resource "aws_security_group" "aurora" {
  name        = "${local.name}-aurora"
  description = "Postgres access to Aurora from within the VPC."
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "PostgreSQL from within the VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
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
