#!/usr/bin/env bash
# Build the Lambda image, push it to ECR, deploy with Terraform and upload the index.
# Requires: aws cli (configured), terraform, docker, and infra/terraform/terraform.tfvars
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
TF="terraform -chdir=infra/terraform"

$TF init -input=false
# 1) The registry must exist before an image can be pushed.
$TF apply -input=false -auto-approve -target=aws_ecr_repository.app -target=aws_s3_bucket.index

REPO="$($TF output -raw ecr_repository)"
BUCKET="$($TF output -raw index_bucket)"
REGION="$(echo "$REPO" | cut -d. -f4)"

# 2) Build and push the image (amd64: Lambda's x86_64 runtime).
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%/*}"
docker build --platform linux/amd64 --provenance=false -f Dockerfile.lambda -t "$REPO:$TAG" .
docker push "$REPO:$TAG"

# 3) Build the index locally and upload it to S3.
S3_BUCKET="$BUCKET" .venv/bin/python -m app.rag.ingest --upload

# 4) Deploy everything else pointing at the new image.
$TF apply -input=false -auto-approve -var "image_tag=$TAG"
echo "Deployed: $($TF output -raw url)"
