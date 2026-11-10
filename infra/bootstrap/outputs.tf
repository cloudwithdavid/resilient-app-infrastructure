output "state_bucket_name" {
  description = "S3 bucket name for the CP1 Terraform state backend."
  value       = aws_s3_bucket.tf_state.id
}

output "state_bucket_arn" {
  description = "ARN of the CP1 Terraform state bucket."
  value       = aws_s3_bucket.tf_state.arn
}
