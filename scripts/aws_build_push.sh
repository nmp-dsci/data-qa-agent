#!/usr/bin/env bash
# Build the four service images for linux/amd64 and push them to ECR.
# Frontend is NOT here — it's a static Vite build served from S3/CloudFront
# (see scripts/deploy_frontend.sh).
#
#   ./scripts/aws_build_push.sh              # tag = current git short sha
#   TAG=v1 ./scripts/aws_build_push.sh       # explicit tag
#
# Requires a live SSO session: aws sso login --profile data-qa
set -euo pipefail
cd "$(dirname "$0")/.."

AWS_PROFILE="${AWS_PROFILE:-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
ACCOUNT_ID="${ACCOUNT_ID:-089783391188}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
export AWS_PROFILE AWS_REGION

echo "==> ECR login ($REGISTRY)"
aws ecr get-login-password --region "$AWS_REGION" --profile "$AWS_PROFILE" \
  | docker login --username AWS --password-stdin "$REGISTRY"

build_push() {
  local name="$1" ctx="$2" dfile="$3"
  local repo="$REGISTRY/data-qa/${name}"
  echo "==> build+push ${name} -> ${repo}:${TAG}"
  docker buildx build --platform linux/amd64 \
    -f "$dfile" \
    -t "${repo}:${TAG}" -t "${repo}:latest" \
    --push "$ctx"
}

# name           build-context          dockerfile
build_push backend-api   services/backend-api  services/backend-api/Dockerfile
build_push data-agent    services/data-agent   services/data-agent/Dockerfile
build_push data-pipeline .                     services/data-pipeline/Dockerfile
build_push db-migrate    .                     services/db-migrate/Dockerfile

echo "==> pushed all four images at tag: ${TAG}"
