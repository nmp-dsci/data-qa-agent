#!/usr/bin/env bash
# Cloud smoke test — runs against the LIVE AWS demo deployment: backend health,
# the demo door, a replayed answer, the DB-less contract (no database-backed
# route is mounted), and the CloudFront frontend including the static exhibit
# dump the tabs read from.
#
#   ./scripts/cloud_smoke.sh
#
# URLs default to the Terraform outputs; override via BACKEND_URL / FRONTEND_URL.
set -euo pipefail
cd "$(dirname "$0")/.."

AWS_PROFILE="${AWS_PROFILE-data-qa}"
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_REGION
if [ -n "$AWS_PROFILE" ]; then export AWS_PROFILE; else unset AWS_PROFILE; fi

TF_DIR="infra/terraform/demo"
BACKEND_URL="${BACKEND_URL:-$(terraform -chdir="$TF_DIR" output -raw backend_api_url)}"
FRONTEND_URL="${FRONTEND_URL:-$(terraform -chdir="$TF_DIR" output -raw cloudfront_domain)}"
# One file the Evals tab reads; its name follows frontend/src/lib/exhibits.ts.
EXHIBIT_PATH="${EXHIBIT_PATH:-exhibits/admin/eval-runs__limit=50.json}"

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

json_field() { python3 -c 'import json,sys; print(json.load(sys.stdin)['"$1"'])'; }
status_of() { curl -s -m 30 -o /dev/null -w '%{http_code}' "$@"; }

echo "==> cloud smoke against:"
echo "    backend:  $BACKEND_URL"
echo "    frontend: $FRONTEND_URL"

# 1. Backend health + the demo door
check "backend /health ok" \
  "ok" "$(curl -sf -m 30 "$BACKEND_URL/health" | json_field '"status"')"
check "backend /health/db reports disabled (no database)" \
  "disabled" "$(curl -sf -m 30 -H 'X-Client-Channel: web' "$BACKEND_URL/health/db" | json_field '"status"')"
check "backend auth_mode demo" \
  "demo" "$(curl -sf -m 30 "$BACKEND_URL/auth/config" | json_field '"auth_mode"')"
check "backend /me rejects bad token (401)" \
  "401" "$(status_of -H 'Authorization: Bearer bogus' "$BACKEND_URL/me")"
check "backend dev-login is closed (403)" \
  "403" "$(status_of -X POST -H 'Content-Type: application/json' -d '{"username":"admin"}' "$BACKEND_URL/auth/dev-login")"

TOKEN="$(curl -sf -m 30 -X POST "$BACKEND_URL/auth/demo-login" | json_field '"access_token"' || echo "")"
check "demo-login mints a session" "true" "$([ -n "$TOKEN" ] && echo true || echo false)"
AUTH=(-H "Authorization: Bearer $TOKEN")

# 2. A replayed answer end to end
QUESTION="$(curl -sf -m 30 "${AUTH[@]}" "$BACKEND_URL/demo/questions" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["question"])' || echo "")"
check "demo questions listed" "true" "$([ -n "$QUESTION" ] && echo true || echo false)"
ANSWER="$(curl -sf -m 60 "${AUTH[@]}" -X POST -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"question": sys.argv[1]}))' "$QUESTION")" "$BACKEND_URL/ask" || echo "{}")"
check "replay answers with a report" \
  "true" "$(printf '%s' "$ANSWER" | python3 -c 'import json,sys; print(str(bool(json.load(sys.stdin).get("report"))).lower())')"
check "conversation history is empty (nothing persisted)" \
  "[]" "$(curl -sf -m 30 "${AUTH[@]}" "$BACKEND_URL/conversations")"

# 3. The DB-less contract: database-backed surfaces are not mounted at all
check "/sql not mounted (404)" "404" "$(status_of "${AUTH[@]}" -X POST -H 'Content-Type: application/json' -d '{"sql":"select 1"}' "$BACKEND_URL/sql")"
check "/explore/datasets not mounted (404)" "404" "$(status_of "${AUTH[@]}" "$BACKEND_URL/explore/datasets")"
check "/admin/eval-goldens not mounted (404)" "404" "$(status_of "${AUTH[@]}" "$BACKEND_URL/admin/eval-goldens")"
check "backend /mcp mounted and gated (401)" \
  "401" "$(status_of -X POST "$BACKEND_URL/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{}')"

# 4. Frontend serves from CloudFront (SPA fallback + the static exhibit dump)
check "frontend 200" "200" "$(status_of "$FRONTEND_URL/")"
check "frontend SPA fallback 200" "200" "$(status_of "$FRONTEND_URL/chat")"
check "exhibit dump served ($EXHIBIT_PATH)" \
  "true" "$(curl -sf -m 30 "$FRONTEND_URL/$EXHIBIT_PATH" | python3 -c 'import json,sys; json.load(sys.stdin); print("true")' 2>/dev/null || echo false)"

echo "==> smoke: $PASS passed, $FAIL failed"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "passed=$PASS"
    echo "total=$((PASS + FAIL))"
  } >> "$GITHUB_OUTPUT"
fi
[ "$FAIL" -eq 0 ]
