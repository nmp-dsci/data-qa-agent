#!/usr/bin/env bash
# Cloud smoke test — runs against the LIVE AWS deployment (no Google login
# needed): health endpoints, auth config, the agent's token guard, a real
# governed SQL query through the agent, and the CloudFront frontend.
#
#   ./scripts/cloud_smoke.sh
#
# URLs default to the Terraform outputs; override via BACKEND_URL / AGENT_URL /
# FRONTEND_URL. Needs AWS creds (SSO profile locally, OIDC in CI) to read the
# agent shared token from Secrets Manager.
set -euo pipefail
cd "$(dirname "$0")/.."

AWS_PROFILE="${AWS_PROFILE-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_REGION
if [ -n "$AWS_PROFILE" ]; then export AWS_PROFILE; else unset AWS_PROFILE; fi

TF_DIR="infra/terraform/foundations"
BACKEND_URL="${BACKEND_URL:-$(terraform -chdir="$TF_DIR" output -raw backend_api_url)}"
AGENT_URL="${AGENT_URL:-$(terraform -chdir="$TF_DIR" output -raw data_agent_url)}"
FRONTEND_URL="${FRONTEND_URL:-$(terraform -chdir="$TF_DIR" output -raw cloudfront_domain)}"

PASS=0
FAIL=0

check() { # name, expected, actual
  if [ "$2" = "$3" ]; then
    echo "  ✔ $1"
    PASS=$((PASS + 1))
  else
    echo "  ✘ $1 — expected [$2], got [$3]"
    FAIL=$((FAIL + 1))
  fi
}

echo "==> cloud smoke against:"
echo "    backend:  $BACKEND_URL"
echo "    agent:    $AGENT_URL"
echo "    frontend: $FRONTEND_URL"

# 1. Backend health + auth mode
check "backend /health ok" \
  "ok" "$(curl -sf -m 30 "$BACKEND_URL/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
check "backend auth_mode google" \
  "google" "$(curl -sf -m 30 "$BACKEND_URL/auth/config" | python3 -c 'import json,sys; print(json.load(sys.stdin)["auth_mode"])')"
check "backend /me rejects bad token (401)" \
  "401" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer bogus' "$BACKEND_URL/me")"

# 2. Agent health + token guard
check "agent /health ok" \
  "ok" "$(curl -sf -m 30 "$AGENT_URL/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
check "agent rejects unauthenticated (401)" \
  "401" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' "$AGENT_URL/agent/config")"

# 3. Governed query through the agent's executor — proves the whole
#    agent -> guardrails -> Aurora (TLS) path without depending on RLS grants
#    (a synthetic user legitimately sees 0 rows from the marts). Retries once
#    after 60s: the first hit after idle can catch the Aurora resume.
TOKEN=$(aws secretsmanager get-secret-value --secret-id data-qa/agent-shared-token \
  --query SecretString --output text)
SQL='{"sql": "select 1 as n", "user": {"id": "00000000-0000-0000-0000-000000000000", "role": "user"}}'
run_sql() {
  curl -sf -m 120 -X POST "$AGENT_URL/agent/sql" \
    -H "Content-Type: application/json" -H "X-Agent-Token: $TOKEN" -d "$SQL" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("rows" if d.get("row_count",0) >= 1 and d.get("error") is None else "bad: %s" % d.get("error"))'
}
RESULT=$(run_sql || echo "request-failed")
if [ "$RESULT" != "rows" ]; then
  echo "  … first agent query failed ($RESULT) — retrying in 60s (Aurora resume)"
  sleep 60
  RESULT=$(run_sql || echo "request-failed")
fi
check "agent SQL executor reaches Aurora" "rows" "$RESULT"

# 4. Frontend serves from CloudFront (SPA fallback too)
check "frontend 200" \
  "200" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' "$FRONTEND_URL/")"
check "frontend SPA fallback 200" \
  "200" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' "$FRONTEND_URL/chat")"

echo "==> smoke: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
