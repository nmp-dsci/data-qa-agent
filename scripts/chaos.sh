#!/usr/bin/env bash
# s40 M2: chaos drills for the queue seam. Run against a `make queue-up` stack.
#
#   ./scripts/chaos.sh kill-worker    # docker-kill one worker mid-job (E8 / C-series rehearsal)
#   ./scripts/chaos.sh poison-job     # inject a malformed job; expect DLQ after MAX_DELIVERIES
#   ./scripts/chaos.sh flood [N]      # N rapid /ask/stream opens (default 30); expect clean 429s
#   ./scripts/chaos.sh dlq            # show the dead-letter stream
#
# Each drill states what "healthy" looks like so a run is pass/fail, not vibes.
set -euo pipefail

API="http://localhost:${API_HOST_PORT:-8000}"
REDIS() { docker compose --profile queue exec -T redis redis-cli "$@"; }

case "${1:-}" in
  kill-worker)
    worker=$(docker ps --format '{{.Names}}' | grep agent-worker | head -1)
    [ -n "$worker" ] || { echo "no agent-worker running (make queue-up WORKERS=2)"; exit 1; }
    echo "killing $worker — healthy: within ~$((30 + 5))s a survivor reclaims the job,"
    echo "the client sees status:restarted, zero jobs lost, no duplicate query_runs row."
    docker kill "$worker"
    ;;
  poison-job)
    echo "injecting a malformed job — healthy: each delivery errors, the reaper"
    echo "redelivers, and after MAX_DELIVERIES it lands on agent:dlq (see: $0 dlq)."
    REDIS XADD agent:jobs '*' job '{"job_id":"poison-'"$(date +%s)"'","enqueued_ms":0,"deadline_ts":9999999999,"request":{"question":null}}'
    ;;
  flood)
    n="${2:-30}"
    echo "opening $n rapid asks — healthy: depth-bound accepted, the rest shed as"
    echo "fast 429s with Retry-After (watch shed/min on the Grafana queue board)."
    token="${DATAQA_TOKEN:-}"
    [ -n "$token" ] || { echo "set DATAQA_TOKEN (a dev login JWT) first"; exit 1; }
    for i in $(seq 1 "$n"); do
      curl -s -o /dev/null -w "%{http_code} " -X POST "$API/ask/stream" \
        -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
        -d '{"question":"chaos flood '"$i"': median rent trend?"}' --max-time 2 &
    done
    wait; echo
    ;;
  dlq)
    REDIS XRANGE agent:dlq - +
    ;;
  *)
    grep '^#   ' "$0" | sed 's/^#   //'
    exit 1
    ;;
esac
