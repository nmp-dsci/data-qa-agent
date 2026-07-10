# --------------------------------------------------------------------------
# One ECR repository per service image. Scan on push; expire untagged images
# so old CI builds don't accumulate storage cost.
# --------------------------------------------------------------------------
resource "aws_ecr_repository" "service" {
  for_each = toset(var.ecr_repositories)

  name                 = "${local.name}/${each.value}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Service = each.value }
}

resource "aws_ecr_lifecycle_policy" "expire_untagged" {
  for_each   = aws_ecr_repository.service
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images older than 14 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}
