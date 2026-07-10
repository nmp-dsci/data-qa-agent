# --------------------------------------------------------------------------
# Source-data bucket: the full ~580MB CSVs land here and the pipeline ECS
# task reads them from S3 instead of the local ./data bind mount (Phase C).
# --------------------------------------------------------------------------
resource "aws_s3_bucket" "source_data" {
  bucket = "${local.name}-source-data-${local.account_id}"
  tags   = { Name = "${local.name}-source-data" }
}

resource "aws_s3_bucket_versioning" "source_data" {
  bucket = aws_s3_bucket.source_data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "source_data" {
  bucket = aws_s3_bucket.source_data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "source_data" {
  bucket                  = aws_s3_bucket.source_data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
