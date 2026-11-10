# Persistent storage for CP1's Terraform state.
resource "aws_s3_bucket" "tf_state" {
  bucket        = "cp1-tfstate-963348789536"
  force_destroy = false # Do not automatically empty the bucket during deletion.

  lifecycle {
    prevent_destroy = true # Reject Terraform plans that destroy or replace this bucket.
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  block_public_acls       = true # Rejects attempts to create public ACL permissions.
  ignore_public_acls      = true # Ignores all public ACL permissions.
  block_public_policy     = true # Rejects new bucket policies that would grant public access.
  restrict_public_buckets = true # Restricts public buckets to authorized same-account and AWS service access.
}

# Deny any request made without secure transport 
data "aws_iam_policy_document" "deny_insecure_transport" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.tf_state.arn,
      "${aws_s3_bucket.tf_state.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

# Attaches policy to s3 bootstrap bucket
resource "aws_s3_bucket_policy" "deny_insecure_transport" {
  bucket = aws_s3_bucket.tf_state.id
  policy = data.aws_iam_policy_document.deny_insecure_transport.json
}
