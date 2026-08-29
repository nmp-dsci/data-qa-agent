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
# `-` not `:-`: an explicitly empty AGENT_URL means "no agent" (demo mode) and
# must not fall through to the terraform lookup.
AGENT_URL="${AGENT_URL-$(terraform -chdir="$TF_DIR" output -raw data_agent_url)}"
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
# s38: demo mode reports "demo" (the walk-in door) while google stays the
# owner door — either is a healthy prod; "dev" or a blank would not be.
AUTH_MODE="$(curl -sf -m 30 "$BACKEND_URL/auth/config" | python3 -c 'import json,sys; print(json.load(sys.stdin)["auth_mode"])' || echo "")"
case "$AUTH_MODE" in google|demo) AUTH_OK="$AUTH_MODE" ;; *) AUTH_OK="google|demo" ;; esac
check "backend auth_mode google|demo ($AUTH_MODE)" "$AUTH_OK" "$AUTH_MODE"
check "backend /me rejects bad token (401)" \
  "401" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer bogus' "$BACKEND_URL/me")"

# 1b. The MCP surface (s36). Credential-free on purpose: the useful signal is
#     401 vs 404. A 404 means the mount failed and the surface silently is not
#     there — the exact failure that would otherwise deploy green, since nothing
#     else in this script would touch it. A 200 would mean it is open to anyone.
check "backend /mcp mounted and gated (401)" \
  "401" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' -X POST "$BACKEND_URL/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')"

# 2+3. Agent health, token guard, and a governed query through its executor
#    (agent -> guardrails -> Aurora over TLS). In demo mode (s38) there is no
#    agent service at all — terraform's data_agent_url output is empty — so
#    these are skipped rather than failed; the deploy after #34 destroyed the
#    agent was the first to trip over this (2026-08-29).
if [ -z "$AGENT_URL" ]; then
  echo "  – agent checks skipped: demo mode, no data-agent service"
else
  check "agent /health ok" \
    "ok" "$(curl -sf -m 30 "$AGENT_URL/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  check "agent rejects unauthenticated (401)" \
    "401" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' "$AGENT_URL/agent/config")"

  # Proves the whole path without depending on RLS grants (a synthetic user
  # legitimately sees 0 rows from the marts). Retries once after 60s: the
  # first hit after idle can catch the Aurora resume.
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
fi

# 4. Frontend serves from CloudFront (SPA fallback too)
check "frontend 200" \
  "200" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' "$FRONTEND_URL/")"
check "frontend SPA fallback 200" \
  "200" "$(curl -s -m 30 -o /dev/null -w '%{http_code}' "$FRONTEND_URL/chat")"

echo "==> smoke: $PASS passed, $FAIL failed"
# s32 W4: hand the counts to the caller so the deploy record carries "smoke 8/8"
# rather than a bare pass/fail — the deck's timeline shows how much was checked.
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "passed=$PASS"
    echo "total=$((PASS + FAIL))"
  } >> "$GITHUB_OUTPUT"
fi
[ "$FAIL" -eq 0 ]
