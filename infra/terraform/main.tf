terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = var.name }
  }
}

data "aws_caller_identity" "me" {}

# --- Container registry -----------------------------------------------------------

resource "aws_ecr_repository" "app" {
  name                 = var.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

# Keep only the last 3 images: ECR storage beyond 500 MB is billed.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 3 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 3 }
      action       = { type = "expire" }
    }]
  })
}

# --- S3 bucket for the vector index ----------------------------------------------

resource "aws_s3_bucket" "index" {
  bucket        = "${var.name}-index-${data.aws_caller_identity.me.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "index" {
  bucket                  = aws_s3_bucket.index.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Secrets: SSM Parameter Store (standard tier is free, Secrets Manager is not) ---

resource "aws_ssm_parameter" "llm_api_key" {
  name  = "/${var.name}/llm_api_key"
  type  = "SecureString"
  value = var.llm_api_key
}

# --- Lambda ------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  name = "${var.name}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Least privilege: read the index and the one parameter, nothing else.
resource "aws_iam_role_policy" "app" {
  name = "app-access"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.index.arn}/index/*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = aws_ssm_parameter.llm_api_key.arn
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 7
}

resource "aws_lambda_function" "app" {
  function_name = var.name
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  architectures = ["x86_64"]
  memory_size   = 1024
  timeout       = 60

  environment {
    variables = {
      LLM_PROVIDER          = var.llm_provider
      EMBED_PROVIDER        = var.embed_provider
      LLM_API_KEY_SSM_PARAM = aws_ssm_parameter.llm_api_key.name
      S3_BUCKET             = aws_s3_bucket.index.bucket
      S3_INDEX_PREFIX       = "index/"
      ODOO_MODE             = "mock"
      API_TOKEN             = var.api_token
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy_attachment.logs]
}

# Function URL: an HTTPS endpoint without API Gateway (free).
resource "aws_lambda_function_url" "app" {
  function_name      = aws_lambda_function.app.function_name
  authorization_type = "NONE" # access is checked by API_TOKEN in the app
}

# --- Budget alarm: email if the month's cost goes over 1 USD ------------------------

resource "aws_budgets_budget" "monthly" {
  count        = var.alert_email == "" ? 0 : 1
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = "1"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
