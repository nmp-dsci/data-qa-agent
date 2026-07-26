#!/usr/bin/env bash
# Revert App Runner to the previously deployed image (s32 W4, decision Q1).
#
#   ./scripts/rollback_apprunner.sh all            # both services
#   ./scripts/rollback_apprunner.sh backend-api
#   ./scripts/rollback_apprunner.sh data-agent
#
# WHY THIS IS MANUAL. App Runner offers no traffic split and there is no ECS
# service to blue/green — it is an all-at-once managed replace. A real weighted
# canary would mean putting backend-api behind ALB+ECS, which is weeks of work
# fighting the scale-to-zero cost design the whole deployment is built around. So
# the plan chose: record every deploy, and make reverting one command. An
# alarm-state auto-gate is a noted later upgrade, not a gap nobody saw.
#
# HOW IT WORKS. Services deploy from `:latest`, so "the previous version" is not a
# tag — it is the image DIGEST that ECR held before the current push. The rollback
# repoints the service at that digest by immutable reference, which App Runner
# treats as a normal deployment. Pinning by digest is the point: rolling back to
# `:latest` would redeploy exactly the thing being rolled back.
#
# The revert is recorded to app.deploy_events (status rolled_back) so the ops
# deck's timeline shows it, rather than a mystery gap between two deploys.
#
# Requires a live SSO session: aws sso login --profile data-qa
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-all}"
AWS_PROFILE="${AWS_PROFILE-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_REGION
if [ -n "$AWS_PROFILE" ]; then export AWS_PROFILE; else unset AWS_PROFILE; fi

REPO_PREFIX="data-qa"

case "$TARGET" in
  all) SERVICES="backend-api data-agent" ;;
  backend-api | data-agent) SERVICES="$TARGET" ;;
  *)
    echo "usage: rollback_apprunner.sh [all|backend-api|data-agent]" >&2
    exit 2
    ;;
esac

service_arn() {
  aws apprunner list-services \
    --query "ServiceSummaryList[?ServiceName=='${REPO_PREFIX}-$1'].ServiceArn | [0]" \
    --output text
}

# The two most recent images in the repo, newest first. The current deployment is
# [0]; the one to go back to is [1]. imagePushedAt ordering is what makes this
# "previous" rather than "some older build".
previous_digest() {
  aws ecr describe-images --repository-name "${REPO_PREFIX}/$1" \
    --query 'sort_by(imageDetails,&imagePushedAt)[-2].imageDigest' --output text
}

current_image() {
  aws apprunner describe-service --service-arn "$1" \
    --query 'Service.SourceConfiguration.ImageRepository.ImageIdentifier' --output text
}

ROLLED_BACK=""
for svc in $SERVICES; do
  ARN="$(service_arn "$svc")"
  if [ -z "$ARN" ] || [ "$ARN" = "None" ]; then
    echo "!! no App Runner service named ${REPO_PREFIX}-${svc}" >&2
    exit 1
  fi

  DIGEST="$(previous_digest "$svc")"
  if [ -z "$DIGEST" ] || [ "$DIGEST" = "None" ]; then
    echo "!! ${svc}: ECR holds fewer than two images — nothing to roll back to" >&2
    exit 1
  fi

  ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
  REGISTRY="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  TARGET_IMAGE="${REGISTRY}/${REPO_PREFIX}/${svc}@${DIGEST}"

  echo "==> ${svc}"
  echo "    current : $(current_image "$ARN")"
  echo "    rollback: ${TARGET_IMAGE}"

  # A digest reference cannot be auto-deployed (auto-deploy watches a tag), so
  # that is switched off for the duration of the rollback. Re-enable it by
  # deploying normally again — `terraform apply` restores auto_deployments_enabled
  # from apprunner.tf, which is also what un-pins the service from the digest.
  aws apprunner update-service --service-arn "$ARN" \
    --source-configuration "ImageRepository={ImageIdentifier=${TARGET_IMAGE},ImageRepositoryType=ECR},AutoDeploymentsEnabled=false" \
    --query 'OperationId' --output text

  ROLLED_BACK="${ROLLED_BACK}${svc} "
done

echo "==> waiting for the rollback deployment(s) to settle"
./scripts/wait_apprunner.sh "rollback" || true

# Record it, so the deck's timeline shows a revert instead of a gap. Best-effort:
# the script exits 0 even when the token is unset or the API is unreachable.
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if command -v uv >/dev/null; then
  uv run python scripts/ops_ingest.py deploy \
    --sha "$GIT_SHA" --status rolled_back \
    --actor "${USER:-operator}" \
    --notes "manual rollback to previous image digest: ${ROLLED_BACK}" || true
fi

cat <<'NEXT'

==> rolled back. Two things to remember:

    1. Auto-deploy is now OFF on the rolled-back service(s) and the image is
       pinned to a digest. The next `terraform apply` (or a merge to main)
       restores auto-deploy and un-pins it — which is also how you roll forward.
    2. This did NOT revert database migrations. They are written to be additive
       and backward-compatible for exactly this reason; undo one deliberately
       with alembic if you really need to.
NEXT
