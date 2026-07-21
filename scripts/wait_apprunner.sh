#!/usr/bin/env bash
# Wait until both App Runner services are RUNNING (no operation in progress).
#
#   ./scripts/wait_apprunner.sh "reason shown in the log"
#
# Pushing :latest auto-deploys both services, and App Runner rejects *any*
# UpdateService while an operation is in progress. The deploy hit exactly that:
# terraform apply ran seconds after the image push, tried to change the agent's
# instance size, and got `InvalidStateException: OPERATION_IN_PROGRESS` — the
# sizing change silently didn't land (2026-07-21). So this settle-wait runs
# BEFORE terraform apply as well as before the smoke test.
#
# The first few polls are ignored on purpose: right after a push there is a
# short window where the auto-deploy hasn't started yet and the services still
# report RUNNING — trusting that would recreate the race with extra steps.
set -euo pipefail

REASON="${1:-services to settle}"
# Local runs default to the data-qa SSO profile; CI sets AWS_PROFILE="" which
# means "use the ambient (OIDC) creds" — but the raw aws CLI would treat the
# empty string as a profile named "", so unset it in that case.
AWS_PROFILE="${AWS_PROFILE-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_REGION
if [ -n "$AWS_PROFILE" ]; then export AWS_PROFILE; else unset AWS_PROFILE; fi

MIN_POLLS=3   # never trust RUNNING before the auto-deploy had time to start
MAX_POLLS=60  # x20s = 20 minutes
echo "==> waiting for App Runner ($REASON)"
for i in $(seq 1 "$MAX_POLLS"); do
  B=$(aws apprunner list-services --query "ServiceSummaryList[?ServiceName=='data-qa-backend-api'].Status" --output text)
  A=$(aws apprunner list-services --query "ServiceSummaryList[?ServiceName=='data-qa-data-agent'].Status" --output text)
  echo "    backend=$B agent=$A"
  if [ "$B" = "RUNNING" ] && [ "$A" = "RUNNING" ] && [ "$i" -gt "$MIN_POLLS" ]; then
    echo "==> settled"
    exit 0
  fi
  sleep 20
done
echo "services did not settle in time" >&2
exit 1
