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

# Local runs default to the data-qa SSO profile; CI sets AWS_PROFILE="".
AWS_PROFILE="${AWS_PROFILE-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_REGION
if [ -n "$AWS_PROFILE" ]; then export AWS_PROFILE; else unset AWS_PROFILE; fi

: "${VITE_API_URL:?Set VITE_API_URL to the backend-api URL (baked into the bundle)}"

TF_DIR="infra/terraform/foundations"
BUCKET="${FRONTEND_BUCKET:-$(terraform -chdir="$TF_DIR" output -raw frontend_bucket)}"
DIST_ID="${CLOUDFRONT_DISTRIBUTION_ID:-$(terraform -chdir="$TF_DIR" output -raw cloudfront_distribution_id)}"

echo "==> building frontend (VITE_API_URL=$VITE_API_URL)"
( cd frontend && npm ci && VITE_API_URL="$VITE_API_URL" npm run build )

echo "==> syncing dist/ -> s3://$BUCKET"
# Hashed assets get a long cache; index.html must never be cached (it points at
# the current asset hashes).
#
# Deliberately NO --delete: hashed chunks are immutable, and a browser tab
# opened before this deploy still lazy-imports the *previous* deploy's
# ExplorePage/SqlEditor/Choropleth chunk on first click. With --delete (plus the
# full CloudFront invalidation below) that request fell through to the SPA
# fallback — index.html served as HTTP 200 text/html — and the import failed
# with "Failed to fetch dynamically imported module": the Explore tab silently
# refused to open until a manual reload (prod incident, 2026-07-21). Keeping a
# few deploys' worth of stale chunks costs cents; deleting them breaks every
# open session on every deploy.
aws s3 sync frontend/dist "s3://$BUCKET" \
  --exclude index.html --cache-control "public,max-age=31536000,immutable"
aws s3 cp frontend/dist/index.html "s3://$BUCKET/index.html" \
  --cache-control "no-cache"

echo "==> invalidating CloudFront ($DIST_ID)"
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" >/dev/null

echo "==> frontend deployed"
