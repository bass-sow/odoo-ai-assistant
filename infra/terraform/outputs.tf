output "url" {
  value = aws_lambda_function_url.app.function_url
}

output "ecr_repository" {
  value = aws_ecr_repository.app.repository_url
}

output "index_bucket" {
  value = aws_s3_bucket.index.bucket
}
