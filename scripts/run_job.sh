#!/usr/bin/env bash
# Run a one-shot ECS job (migrate | pipeline) and stream its exit status.
#
#   ./scripts/run_job.sh migrate
#   ./scripts/run_job.sh pipeline
#
# Requires a live SSO session: aws sso login --profile data-qa
set -euo pipefail
cd "$(dirname "$0")/.."

JOB="${1:?usage: run_job.sh <migrate|pipeline>}"
AWS_PROFILE="${AWS_PROFILE:-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_PROFILE AWS_REGION

TF_DIR="infra/terraform/foundations"
SUBNETS=$(terraform -chdir="$TF_DIR" output -json private_subnet_ids | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)))')
SG=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=data-qa-jobs" --query 'SecurityGroups[0].GroupId' --output text)

echo "==> run-task data-qa-${JOB} (subnets: $SUBNETS)"
TASK_ARN=$(aws ecs run-task \
  --cluster data-qa \
  --task-definition "data-qa-${JOB}" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
  --query 'tasks[0].taskArn' --output text)
echo "    task: $TASK_ARN"

echo "==> waiting for the task to stop (pipeline can take a while on ~3M rows)"
# The waiter caps at ~10 min per call — loop it until the task actually stops.
until aws ecs wait tasks-stopped --cluster data-qa --tasks "$TASK_ARN" 2>/dev/null; do
  STATUS=$(aws ecs describe-tasks --cluster data-qa --tasks "$TASK_ARN" \
    --query 'tasks[0].lastStatus' --output text)
  echo "    still ${STATUS} ($(date +%H:%M:%S)) — waiting..."
  [ "$STATUS" = "STOPPED" ] && break
done

EXIT_CODE=$(aws ecs describe-tasks --cluster data-qa --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text)
STOP_REASON=$(aws ecs describe-tasks --cluster data-qa --tasks "$TASK_ARN" \
  --query 'tasks[0].stoppedReason' --output text)

echo "==> exit code: $EXIT_CODE (reason: $STOP_REASON)"
echo "    logs: aws logs tail /ecs/data-qa-${JOB} --since 1h --profile $AWS_PROFILE"
[ "$EXIT_CODE" = "0" ]
