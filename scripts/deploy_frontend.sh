#!/usr/bin/env bash
# Build the frontend (Vite) with the cloud API URL baked in, upload it to the
# frontend S3 bucket, and invalidate the CloudFront cache.
#
# The API URL is inlined at build time (Vite bundles VITE_* into the JS), so a
# rebuild is required whenever the backend URL changes.
#
#   VITE_API_URL=https://<backend>.awsapprunner.com ./scripts/deploy_frontend.sh
#
# Bucket + distribution id default to the Terraform outputs; override via env.
# Requires a live SSO session: aws sso login --profile data-qa
set -euo pipefail
cd "$(dirname "$0")/.."

AWS_PROFILE="${AWS_PROFILE:-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_PROFILE AWS_REGION

: "${VITE_API_URL:?Set VITE_API_URL to the backend-api URL (baked into the bundle)}"

TF_DIR="infra/terraform/foundations"
BUCKET="${FRONTEND_BUCKET:-$(terraform -chdir="$TF_DIR" output -raw frontend_bucket)}"
DIST_ID="${CLOUDFRONT_DISTRIBUTION_ID:-$(terraform -chdir="$TF_DIR" output -raw cloudfront_distribution_id)}"

echo "==> building frontend (VITE_API_URL=$VITE_API_URL)"
( cd frontend && npm ci && VITE_API_URL="$VITE_API_URL" npm run build )

echo "==> syncing dist/ -> s3://$BUCKET"
# Hashed assets get a long cache; index.html must never be cached (it points at
# the current asset hashes).
aws s3 sync frontend/dist "s3://$BUCKET" --delete \
  --exclude index.html --cache-control "public,max-age=31536000,immutable"
aws s3 cp frontend/dist/index.html "s3://$BUCKET/index.html" \
  --cache-control "no-cache"

echo "==> invalidating CloudFront ($DIST_ID)"
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" >/dev/null

echo "==> frontend deployed"
