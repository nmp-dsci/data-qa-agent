#!/usr/bin/env bash
# Build the demo image (backend-api, DB-less) for linux/amd64 and push it to
# ECR. Frontend is NOT here — it's a static Vite build served from S3/CloudFront
# (see scripts/deploy_frontend.sh). The data-agent, pipeline and migrate images
# went with the database (s52): the demo replays a baked-in pack and reads a
# static exhibit dump, so one image is the whole backend.
#
#   ./scripts/aws_build_push.sh              # tag = current git short sha
#   TAG=v1 ./scripts/aws_build_push.sh       # explicit tag
#
# Requires a live SSO session: aws sso login --profile data-qa
set -euo pipefail
cd "$(dirname "$0")/.."

# Local runs default to the data-qa SSO profile; CI (OIDC env creds) sets
# AWS_PROFILE="" and the CLI falls back to the ambient credentials.
AWS_PROFILE="${AWS_PROFILE-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
ACCOUNT_ID="${ACCOUNT_ID:-089783391188}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
REPO="$REGISTRY/data-qa/demo"
export AWS_REGION
if [ -n "$AWS_PROFILE" ]; then export AWS_PROFILE; else unset AWS_PROFILE; fi

echo "==> ECR login ($REGISTRY)"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> build+push demo -> ${REPO}:${TAG}"
docker buildx build --platform linux/amd64 \
  -f services/backend-api/Dockerfile \
  -t "${REPO}:${TAG}" -t "${REPO}:latest" \
  --push services/backend-api

echo "==> pushed demo image at tag: ${TAG}"
