#!/usr/bin/env bash
# Block until the demo App Runner service is RUNNING again after a deployment.
#
#   ./scripts/wait_apprunner.sh "<why we are waiting>"
#
# The first few RUNNING polls are deliberately distrusted: start-deployment
# returns before the service flips to OPERATION_IN_PROGRESS, so an immediate
# RUNNING is the *old* instance, not the new one (2026-07-21).
set -euo pipefail
REASON="${1:-service to settle}"
AWS_PROFILE="${AWS_PROFILE-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
SERVICE_NAME="${SERVICE_NAME:-data-qa-demo}"
export AWS_REGION
if [ -n "$AWS_PROFILE" ]; then export AWS_PROFILE; else unset AWS_PROFILE; fi
MIN_POLLS=3   # never trust RUNNING before the deployment had time to start
MAX_POLLS=45  # x20s = 15 minutes
echo "==> waiting for App Runner $SERVICE_NAME ($REASON)"
for i in $(seq 1 "$MAX_POLLS"); do
  S=$(aws apprunner list-services --query "ServiceSummaryList[?ServiceName=='$SERVICE_NAME'].Status" --output text)
  echo "    $SERVICE_NAME=$S"
  if [ "$S" = "RUNNING" ] && [ "$i" -gt "$MIN_POLLS" ]; then
    echo "==> settled"
    exit 0
  fi
  sleep 20
done
echo "service did not settle in time" >&2
exit 1
