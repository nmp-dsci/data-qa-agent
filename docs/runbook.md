# Runbook — Data Pilot in production

**Audience:** whoever is holding the pager, which is one person. Last reviewed
2026-07-27 (s32 W2).

Start at **`/ops`** — the flight deck answers "is it healthy, safe, fast and
affordable?" in one screen, and every symptom below maps to a lamp on it. Logfire
is the microscope for a single slow run; CloudWatch is the last resort.

> **Know this before you start:** the three CloudWatch alarms publish to SNS
> topics whose email subscriptions were never confirmed, so **nothing pages you**.
> You will find out about an incident by looking, or because someone tells you.
> `/ops` exists to make looking cheap. Confirming SNS is a tracked, non-blocking
> chore.

---

## 0 · Orientation

| What | Where |
|------|-------|
| App (frontend) | CloudFront URL — `terraform output cloudfront_domain` |
| API | `terraform output backend_api_url` |
| Ops deck | `<app>/ops` (admin only) |
| Traces | Logfire project → search the `otel_trace_id` from the deck's slow-ask table |
| Logs | `aws logs tail /aws/apprunner/data-qa-backend-api/…` · `/ecs/data-qa-{migrate,pipeline}` |
| Deploys | GitHub Actions → *Deploy (AWS)*; outcomes mirrored to the deck's timeline |
| Region / account | `ap-southeast-2` · `089783391188` (SSO profile `data-qa`) |

**Two facts that shape every response below:**

1. **Aurora Serverless v2 sits at min 0 ACU and auto-pauses after an idle hour.**
   A resume takes ~30s. Cold-start slowness is normal, not an incident.
2. **App Runner has no traffic split.** There is no canary and no automatic
   rollback; reverting is a command you run (`make rollback`).

---

## 1 · "The app is slow"

**Check first:** the deck's `ttfp p95` and `full answer p95` tiles, and the
`marts` lamp.

| Reading | Meaning | Action |
|---------|---------|--------|
| `ttfp p95` normal, `full answer p95` high | Expected. Full-answer time is **extract-bound** (~96s p95 in prod) — that is the agent doing real work over a wide extract, an eval-loop lever, not an infra fault | None. If it needs improving, that is `make eval` and a narrower extract, not a bigger instance |
| `ttfp p95` high (SLO-B lamp warn/bad) | The felt latency has regressed. Something before the first page is slow | Open the deck's slowest-ask table, take an `otel_trace_id`, look at the span waterfall: is it the model, the SQL, or the agent hop? |
| First request of the day slow, then fine | Aurora resumed from auto-pause | None. `db cold starts` on the deck counts these |
| `saturation` shows agent CPU pinned | The sandbox is CPU-bound and 2 vCPU is the ceiling | Consider a bigger `agent_cpu`; it is a spend decision, already tuned once (1→2 vCPU, 2026-07-21) |

---

## 2 · "Answers are failing"

**Check first:** the `errors` lamp and the *errors by surface* panel.

1. **Degraded, not errored?** A `degraded` run means the answer came back but not
   the one that was asked for — the model policy retried and then fell through to
   the deterministic stub, or the agent was unreachable. The user saw a sentence
   and a **Retry** button, not a stack trace. Check the `PROVIDER`/errors panel
   for a burst.
2. **Provider outage.** `attempts_mean` above 1 means the LLM provider is flaky
   and the retries are absorbing it. Nothing to do while the graph is flat.
3. **Agent unreachable (`engine: unavailable`).** The backend→agent hop retried a
   connection failure up to 3 times and gave up — a timeout is deliberately *not*
   retried here, since it means the request reached the agent and its own retry
   policy may already be mid-flight on a slow answer; retrying both would just
   stack latency and spend. Check the agent service:
   ```bash
   curl -s "$(terraform -chdir=infra/terraform/foundations output -raw data_agent_url)/health"
   aws apprunner list-operations --service-arn <agent-arn> --max-results 5
   ```
   Most often a deployment in progress; wait it out.
4. **Everything failing with `503 db_warming`.** Aurora is resuming. The client
   retries this for up to 75s automatically. If it persists past a minute, check
   the cluster:
   ```bash
   aws rds describe-db-clusters --db-cluster-identifier data-qa --query \
     'DBClusters[0].[Status,ServerlessV2ScalingConfiguration]'
   ```

---

## 3 · "Explore is broken" / "the data looks wrong"

This has happened, and the cause was **stale marts** — prod froze 12 days behind
while deploys kept shipping, `marts.property_yield` was never built, and Explore
500'd on every load.

**Check first:** the deck's `marts` lamp and the *data freshness* panel.

- `marts` red (age > 24h) → the pipeline hasn't run. Run it:
  ```bash
  ./scripts/run_job.sh pipeline      # ~15 min on the full CSVs
  ```
- `dbt tests` short of total → a model built but a data-quality test failed. Read
  the job log (`/ecs/data-qa-pipeline`); the sanity tests
  (`assert_*_has_coverage`, `assert_growth_pct_*`) fail loudly when a mart can't
  support the question type it exists for.
- A 500 naming `UndefinedTableError` → a mart is missing entirely. Same fix.

Every deploy now runs the pipeline, which is why this class of outage should not
recur; the freshness lamp is there because "should not" is not "cannot".

---

## 4 · "The bill is too high"

**Check first:** the deck's `spend / budget` and `cost / answer` tiles.

- Cost is **cache-dominated**: most input tokens are prompt-cache hits, billed
  ~10x cheaper, so `cost / answer` is cache-adjusted and roughly ⅙ of what naive
  token counting would suggest. Compare against `budget`, not intuition.
- A spike in `cost / answer` usually means turns, not prompt size. Check
  `attempts / retries` and the trace's turn count — cutting turns is the lever.
- Immediate brake: lower the per-user daily caps (`ASK_DAILY_LIMIT_FREE` /
  `_PAID` on the backend service) — a Terraform change plus a deploy, or an App
  Runner env edit for an emergency.
- The billing alarm (>$USD threshold) fires to an unconfirmed SNS topic, so treat
  the deck tile as the real signal.

---

## 5 · "A deploy went wrong"

Deploys are **recorded, not gated** (decision Q1: App Runner has no traffic
split, so a weighted canary would mean ALB+ECS — weeks of work fighting the
scale-to-zero cost design).

1. Look at the deck's **deploy timeline**: sha, duration, smoke result.
2. If the new version is bad, revert on command:
   ```bash
   make rollback                 # both services, to the previous image
   make rollback SERVICE=backend-api
   ```
   This re-points App Runner at the previously deployed image digest and records a
   `rolled_back` row, so the timeline shows what happened.
3. **A rollback does not revert migrations.** Migrations are written to be
   additive and backward-compatible for exactly this reason; if you need to undo
   one, do it deliberately with `alembic downgrade`, not as part of a rollback.
4. Then fix forward: the rollback bought time, it did not resolve anything.

---

## 6 · "Did someone attack us?"

**Check first:** the deck's `denials`, `auth fails` and `redteam` readouts.

- `denials` counts guard refusals (`security_denied` events) across chat and the
  SQL editor. A handful is normal — models generate invalid SQL. A spike from one
  user is worth reading: `SELECT * FROM app.query_runs WHERE status = 'error'
  ORDER BY created_at DESC` in the admin SQL editor.
- `auth fails` counts failed sign-ins. Google is the IdP, so a spike is theirs to
  rate-limit, not ours.
- `redteam` is the last pack run, not live. Re-run it after any guard change:
  ```bash
  make redteam            # real model traffic, costs tokens
  make injection-suite    # deterministic, free, also runs in CI
  ```
- The threat model and what is deliberately out of scope: [`SECURITY.md`](../SECURITY.md).

---

## 7 · Routine operations

```bash
# Recompute the ops deck now (it self-refreshes when read, this forces it)
make ops-rollup

# Load numbers
make loadtest                                   # browse, 20 VUs
make loadtest SCENARIO=chat VUS=3 DURATION=60s   # the real /ask path

# Score answer quality against the goldens
make eval

# Sample live answer quality (advisory drift signal)
python scripts/ops_judge_sample.py --limit 10
```

### Turning tracing on (first-time setup)

Prod ships spans only once the token has a real value — the Terraform-created
secret is a placeholder, exactly like the LLM key:

```bash
aws secretsmanager put-secret-value --profile data-qa \
  --secret-id data-qa/logfire-token --secret-string '<write token>'
# App Runner reads secrets at instance launch, so force a new deployment:
aws apprunner start-deployment --service-arn <backend-arn>
```

### Turning the Tier-2 saturation panel on

```hcl
# infra/terraform/foundations/terraform.tfvars
ops_cloudwatch_enabled = true
```

The flag and the IAM read grant move together, so the pull can never be enabled
without permission. Without it the deck renders full Tier-1 (Postgres)
telemetry — the panel just reads "n/a".

---

## 8 · What this runbook cannot do for you

Stated so the gaps are decisions, not surprises:

- **No paging.** SNS subscriptions unconfirmed; `/ops` is a pull surface.
- **No automatic rollback.** `make rollback` needs a human (decision Q1).
- **No staging environment.** `main` deploys to the only environment there is.
- **No on-call rotation, no MTTA.** One operator.
