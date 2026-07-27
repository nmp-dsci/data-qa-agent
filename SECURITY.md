# Security — threat model & guarantees

**data-qa-agent / Data Pilot** · last reviewed 2026-07-27 (s32 W3)

This app lets people ask questions in natural language and answers them by running
generated SQL against a shared database. That is a genuinely dangerous shape, so
this page states plainly what is defended, how it is *proven* to be defended, and
what is deliberately out of scope. Enforcement without evidence is a claim; the
"Proven by" column is the difference.

---

## 1 · What is worth attacking

| Asset | Why it matters | Blast radius if lost |
|-------|----------------|----------------------|
| **Other users' rows** (`conversations`, `messages`, `query_runs`, `user_memories`) | Free-text questions are the only PII in the system | One user reads another's questions and stored preferences |
| **Write access to any table** | The agent generates SQL from untrusted text | Data loss / privilege escalation via `app.users.role` |
| **The RLS session variable** (`app.current_user_id`) | Every isolation policy reads it | Whoever can set it chooses whose rows they see |
| **Provider API keys & DB credentials** | Direct financial and data loss | Unbounded model spend; full database access |
| **Model spend** | An LLM is the dominant cost | A runaway loop is a bill, not an outage |

The **marts themselves are public NSW property records**. Reading them is not a
breach, which is why the scope below is narrow and the PII work is a regex pass
rather than a pipeline (decision Q4).

---

## 2 · Controls, and how each is proven

| Threat | Control | Proven by |
|--------|---------|-----------|
| Unauthenticated access | Google OIDC ID tokens verified against Google's JWKS per request; a signed dev stub in local mode only | `evals/journeys.yaml` auth journeys · `scripts/smoke_test.py` |
| Cross-user data access | **Postgres RLS** on every `app.*` table, scoped by `SET LOCAL app.current_user_id` inside each request transaction — enforced by the database, so an app-code bug cannot leak rows | RLS isolation journeys (user2 must never see user1's rows) |
| The agent writing data | The agent connects as **`agent_ro`** (read-only). Admin SQL-editor reads use `admin_ro` (BYPASSRLS, still SELECT-only) | Role grants in migrations `0001`/`0012`; DML payloads in `tests/security/test_injection.py` |
| Generated / typed SQL doing more than reading | Three stacked layers: shape check (SELECT/WITH, single statement) → keyword denylist over code only → **sqlglot AST walk** rejecting DML/DDL, `exp.Command`, and CTE-hidden mutations | `tests/security/test_injection.py` (43 cases, zero-LLM, blocks every merge) |
| **Rewriting the RLS context from inside a SELECT** | `set_config` and friends are denied by name in the AST guard. This is not covered by the read-only role — `set_config` needs no write privilege — and it parses as an ordinary `Select`, so no node-type check catches it | `test_rls_bypass_attempts_are_refused` |
| Filesystem / network escape from SQL | `pg_read_file`, `lo_export`, `dblink`, `pg_sleep`, … denied by the same function list | same |
| Model-written analysis code escaping | Analysis runs in **Pyodide/WASM**: no syscalls, no host filesystem, no network. Attempt budgets bound retries | `services/data-agent/tests/test_pyodide_sandbox.py` |
| Runaway query cost | Per-role `statement_timeout` (migration `0018`), a row cap, and a bounded SQL-attempt budget | `agent/config.py` limits |
| Runaway model cost | Tiered per-user daily caps shared across `/ask` and the SQL assist; a request cap and a token ceiling per run | `app/limits.py`; cap hits recorded to the ops deck |
| Prompt injection | The question is the only untrusted input, and it cannot reach SQL except through the guard above. A successful injection still cannot read another user's rows or write anything | `security/promptfoo/redteam.yaml` (`prompt-injection` class) |
| Direct calls to the agent service | The agent's App Runner URL is public with the backend as its only intended caller; `AGENT_SHARED_TOKEN` middleware rejects everything else (except `/health`) | `agent/main.py` middleware |
| Secrets in code or logs | Secrets live in AWS Secrets Manager, injected as env vars; Terraform generates them so no human handles them. Logfire's built-in scrubber plus `app/scrub.py` on persistence | `pii-exfil` red-team class; `tests/test_scrub.py` |
| Denials going unnoticed | Every guard refusal records `status='error'` on the run plus a `security_denied` event, surfaced on `/ops` | `_run_status` in `app/routers/ask.py` |

### Layered, not single-point

The three isolation layers are independent on purpose: **authN** (is this a real
user?), **authZ** (is this endpoint theirs?), and **RLS** (are these rows theirs?).
The third is enforced by Postgres rather than by application code, so a bug in the
first two still cannot leak rows. Likewise the SQL guard and the read-only role
overlap deliberately — either alone would be sufficient for most attacks, and
neither is trusted to be.

---

## 3 · Testing

Two tiers, for the same reason the ops deck has two:

- **`tests/security/test_injection.py`** — deterministic, no LLM, no network. Runs
  in CI on every PR beside the golden-pack gate, so a guard regression cannot
  merge. This is the gate.
- **`security/promptfoo/redteam.yaml`** (`make redteam`) — real model traffic
  through the real `/ask` endpoint, in four attack classes (`rls-bypass`,
  `jailbreak-to-dml`, `prompt-injection`, `pii-exfil`). Costs tokens and needs a
  running stack, so it is a deliberate run; pass rates per class land in
  `app.security_runs` and light the `/ops` red-team bars.

Writing the deterministic suite found two real defects in the guard, which is the
argument for having written it:

1. **`SELECT set_config('app.current_user_id', …)` was accepted.** Read-only in
   form, RLS-context-rewriting in effect, and invisible to the node-type check.
   Fixed with a denied-function list — which had the same gap for a
   double-quoted call (`SELECT "set_config"(...)`, whose name sqlglot
   represents as an `exp.Identifier` rather than a bare string); the
   function-name check now unwraps that before matching.
2. **The keyword denylist scanned string literals**, so a query filtering on the
   address `'GRANT ST'` — a value present in the committed sample data — was
   refused. Over-blocking is a defect too: a guard that rejects real queries gets
   removed, and then nothing guards anything. Fixed by blanking quoted spans
   before the keyword scan, which cannot weaken it (a keyword inside a literal is
   data and can never execute).

---

## 4 · Deliberately out of scope

Named rather than silently omitted:

- **Multi-tenant hostile isolation.** RLS isolates users; this is not built to host
  mutually distrustful organisations sharing infrastructure.
- **WAF / DDoS.** No AWS WAF and no rate limit outside the per-user LLM cap. The
  concurrency ceiling (App Runner `max_concurrency` 100, one instance) is a cost
  decision that also bounds abuse; a public launch would need a real edge.
- **Alert delivery.** Three CloudWatch alarms exist; their SNS email subscriptions
  were never confirmed, so **the alarms currently notify nobody**. The `/ops` deck
  is the working substitute — an operator-pull surface, not a page. Fixing SNS is
  a tracked, non-blocking chore.
- **Automatic rollback.** App Runner has no traffic split, so deploys are recorded
  and reverted on command (`make rollback`), not gated automatically (decision Q1).
- **Heavyweight PII detection.** The data is public; the only PII surface is the
  typed question. Regex plus Logfire's scrubber, sized to the risk (decision Q4).
- **Formal on-call.** One operator, no rotation, no MTTA target.

---

## 5 · Reporting a vulnerability

This is a personal portfolio project, not a hosted service with users to protect.
If you find something, open a GitHub issue — or, if it is genuinely sensitive,
email the address on the repository owner's profile. There is no bounty and no
formal SLA; findings are still welcome, and the interesting ones end up in
`tests/security/` as a regression.
