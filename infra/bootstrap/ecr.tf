# One image, many entrypoints (ADR 0003). Shared by dev and prod so the exact tag
# tested in dev is promoted to prod (ADR 0008).
resource "aws_ecr_repository" "pulse" {
  #checkov:skip=CKV_AWS_136:AES256 (ECR default encryption) is sufficient for a public-source app image; a CMK adds cost and key-policy plumbing for CI.
  name                 = var.ecr_repository_name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "pulse" {
  repository = aws_ecr_repository.pulse.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the last ${var.ecr_keep_tagged_images} tagged images"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = var.ecr_keep_tagged_images
        }
        action = { type = "expire" }
      },
    ]
  })
}
