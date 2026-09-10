# s49 · Eval loop contract

Plan: `.lavish/s49_eval-loop-review-plan.html` (decisions D1–D5 locked 2026-09-10).
This file is the shared contract every s49 workstream codes against. If two
workstreams disagree, this file wins; change it here first.

The loop: NL → `extract` (governed SQL) → `run_analysis` (Pyodide sandbox,
`skills.*` preloaded) → `start_deck` / `add_slide` (Slides + Sheet). Everything
runs on the Claude Agent SDK runtime (`AGENT_RUNTIME=agent_sdk`, sonnet-5).

## Locked decisions

| ID | Decision |
|---|---|
| D1 | Golden = **outcome** (gates) + **checkpoints** (diagnose only, never gate, never shown to the agent). |
| D2 | Judge is **advisory until the pack has 10 goldens** (`HOLDOUT_MIN_CASES`). Pass = G1 + G5. Insight judge (`insight-v1`) is removed. |
| D3 | Knowledge stays a markdown filesystem (`services/data-agent/knowledge/`) plus a DB curator write path that exports to git. CLAUDE.md gets a generated `skills` signature block. No SKILL.md for facts. |
| D4 | Offline optimiser opens **draft PRs only**. Never commits to main or a challenger branch on its own. |
| D5 | One PR for M0–M5. M5 runs the whole loop on **two made-up goldens**. |
| — | Promotion = eval accuracy vs champion: `pass_rate >= champion AND no pass→fail flips` (unchanged, `scripts/mlflow_registry.py`). |
| — | Judge model = sonnet-5, medium effort, same as the agent. Cross-family rule dropped. |

## Migration 0039 (`0039_eval_loop_s49.py`) — owned by the coordinator, do not edit

```
app.query_runs      + artifact_manifest jsonb          -- deck as the agent specified it
app.eval_results    + otel_trace_id text               -- the run's trace id (join to MLflow traces)
                    + mlflow_run_id text               -- the per-case MLflow run
                    + judge jsonb                      -- {label, diagnosis, reason, model, rubric_hash, calibrated}
                    + checkpoints jsonb                -- {sql:{...}, analysis:{...}, deck:{...}} scores
app.eval_cases      + golden_answer text               -- reference answer text (judge input)
                    + label text                       -- 'high' | 'medium' | 'low' (reference label of golden_answer; normally 'high')
                    + calibration_examples jsonb       -- [{label, answer}] curator-written
                    + checkpoints jsonb                -- {sql:{key_cols}, analysis:{expected_skills, derived_cols}, deck:{layouts_any_of, kpi_label_contains}}
app.knowledge_pages       (path text PK, name text, body text, version int, author text, updated_at timestamptz)
app.knowledge_pages_log   (id bigserial PK, path text, version int, body text, author text, action text, created_at timestamptz)
```

Grants mirror 0036 (ordinals): `agent_ro` SELECT on knowledge_pages; `admin_ro` SELECT on both; the app role INSERT/UPDATE.

## Workstreams

### W-A · M0 tracing (Opus, high)

Files: `agent/sdk_trace.py`, `agent/otlp.py`, `agent/sdk_agent.py`, `agent/sandbox/{contract.py,runner.py,pyodide_host.mjs,pyodide_runner.py}`, `agent/sandbox_agent.py` (`_do_run_analysis`), `services/backend-api/app/routers/ask.py`, `scripts/eval_run.py` (id capture only), `agent/main.py` (`/agent/eval/grade` span).

1. **Child spans.** Keep the outer `agent_sdk.answer` span. Open one child span per translated step in `SdkTrace.consume`, with the same attributes the flat trace entry carries:
   - `model.turn` — `turn`, `input_tokens`, `output_tokens`, `cache_read`, `cache_write`, `stop_reason`.
   - `tool.extract` — `sql` (truncate 4 KB), `status`, `row_count`, `frame`, `ms`.
   - `tool.run_analysis` — `code_sha` (sha256[:12] of code), `runtime` (subprocess|pyodide), `status`, `ms`, `skills_used` (csv), `skill_gaps` (count), `stdout_len`, `error` (truncate 1 KB).
   - `tool.Read` / `tool.Grep` / `tool.Glob` — `path`, `knowledge_page`, `denied` (bool), `quota_left`.
   - `tool.start_deck` — `deck_id`; `tool.add_slide` — `index`, `layout`, `chart_type`, `rows`, `has_kpi`.
   - `tool.lookup_values`, `tool.no_answer`, `tool.remember` — `status`.
   Spans use `otlp.agent_span` semantics (no-op when `OTLP_ENDPOINT` unset). Nest under the current span (they are opened inside the outer context).
2. **Sandbox capture.** `AnalysisResult` gains `stdout: str = ""` (capped 8 KB, note truncation) and keeps `frames`. Both executors capture `print` output (redirect `sys.stdout` in the child / in the Pyodide bootstrap — patch BOTH layers, see memory `sandbox-has-two-builtins-layers`). The `analysis` trace step gains `code_sha`, `runtime`, `stdout`, `frames` (heads only: first 20 rows per frame), `ms`.
3. **Deck manifest.** `deps.deck.manifest()` already includes per-slide `spec`. Backend `ask.py` persists the manifest (minus `baseline`) into `query_runs.artifact_manifest`. Expose it on `GET /admin/runs/{id}` (whatever route the Evaluations tab already uses for a run).
4. **Eval ids.** `scripts/eval_run.py` writes `eval_results.otel_trace_id` (read from `query_runs.otel_trace_id` for the case's `query_run_id`) and `eval_results.mlflow_run_id` (the id `log_case_mlflow` creates; refactor so persist happens after MLflow logging, or update the row).
5. **Grade span.** `/agent/eval/grade` opens `agent_span("eval.grade", run_id=..., otel_trace_id=..., case_key=...)` around grading; the runner passes `run_id`/`otel_trace_id`/`case_key` in `GradeRequest`.

Verify: one chat question on the local stack shows ≥ 6 child spans under `agent_sdk.answer` in MLflow (`http://localhost:5500`, experiment `data-qa/traces`); `SELECT artifact_manifest FROM app.query_runs` is populated; sandbox stdout visible in the trace viewer JSON. Unit tests for `SdkTrace` span emission (use the in-memory OTel exporter), `AnalysisResult.stdout` on both runtimes (`SANDBOX_RUNTIME=subprocess` and `pyodide`).

### W-B · M1 versioning (Sonnet, medium)

Files: `agent/version.py`, `scripts/mlflow_registry.py`, `scripts/mlflow_client.py`, new `scripts/agent_checkout.py`, `agent/workspace.py`, `agent/skills/__init__.py`, `tests/`.

1. **skills_hash into the SDK fingerprint.** `build_sdk_fingerprint()` adds `skills_hash()` (the existing function over `agent/skills/*.py`) as a composed component; the returned dict's `skills_hash` key becomes `s-<8>` (real), and the old `ms-` marts/schema digest moves to a new key `marts_schema_hash`. Update the docstring at version.py:80-86 (it is wrong: the sandbox preloads `skills` on both runtimes). Label gains `sk-<6>`.
2. **Generated skills block in CLAUDE.md.** `workspace.py` renders a `## Skills available in run_analysis` section from the `@skill` registry: one line per skill, `signature — first docstring line`. Add a registry accessor in `skills/__init__.py` (`registered() -> list[(module, name, signature, doc)]`). Template slot `{{SKILLS}}` in `agent/prompts/workspace_claude.md` replaces the hand-written list at lines ~39-45 (keep the "NEVER do growth/yield/rolling maths yourself" instruction).
3. **bundle.json + tarball on register.** `mlflow_registry.py cmd_ensure`: for each newly registered fingerprint, log artifacts to the register run: `bundle.json` = `{fingerprint, components:{name: hash}, git_sha, image_tag, files:[...], db_snapshots:{ordinals: <hash>, knowledge_pages: <hash|null>}, created_at}` and `bundle.tar.gz` = `agent/prompts/`, `agent/skills/`, `knowledge/` (exclude `__pycache__`). Tag the model version `bundle_run_id`.
4. **`scripts/agent_checkout.py FP`** (`make agent-checkout FP=av-…`): fetch `bundle.json` from MLflow, `git worktree add .worktrees/<fp> <git_sha>` (if sha known, else extract the tarball into `.worktrees/<fp>/bundle/`), print the compose override needed to run it (`AGENT_WORKTREE=…`). Do not restore DB rows automatically; print the SQL that would.
5. Tests: fingerprint changes when a skill file changes; bundle.json lists every component; checkout of a known fingerprint reproduces `skills/analysis.py` byte-for-byte.

### W-C · M2 goldens + judge (Opus, high)

Files: `scripts/eval_pack.py`, `scripts/eval_run.py`, `agent/eval_graders.py`, `agent/eval_judge.py` (rewrite), `agent/main.py` (`/agent/eval/grade`, `/agent/judge`), `services/backend-api/app/routers/goldens.py` (+ `eval_runs` router), `frontend/src/features/goldens/GraderEditor.tsx`, `frontend/src/features/goldens/graderSpec.ts`, `frontend/src/features/evals/EvalsPage.tsx`, `frontend/src/lib/api.ts`, `evals/cases/*.yaml`, `tests/test_eval_pack.py`.

1. **Golden v2 in the pack.** New YAML keys per case: `golden_answer`, `label`, `calibration_examples`, `checkpoints` (see migration). `eval_pack.py` import/export round-trips them. Existing three cases get `golden_answer` + `label: high` written by hand (derive the answer text from the golden's expected values).
2. **Checkpoint scoring (diagnostic).** In `score_case`, compute `checkpoints`:
   - `sql`: `rows_match` = the agent's primary extract rows (from `query_runs.trace` `sql` steps → re-run via `/sql`? No: compare the agent's `sql_text` result set to `golden_truth()` on `key_cols`; score = F1 of key tuples).
   - `analysis`: `expected_skills ⊆ skills_used` and `derived_cols ⊆` columns of the derived frames in the analysis trace step; score in [0,1].
   - `deck`: from `artifact_manifest`: any slide layout in `layouts_any_of`; some `kpi_label` contains `kpi_label_contains`.
   Persist to `eval_results.checkpoints`. Never gates.
3. **Judge rewrite.** Delete `judge_insight`, `INSIGHT_RUBRIC`, `_CRITERIA`, `judge_choice` cross-family logic. New:
   - `judge_answer(*, question, golden_answer, golden_values, answer, deck_outline, g1) -> {label, diagnosis, reason, model, rubric_hash, rubric_version:"label-v1"}`. `label ∈ {low, medium, high}`, `diagnosis ∈ {sql, analysis, presentation, knowledge, none}`. Model: `claude-sonnet-5` via the Anthropic SDK / pydantic-ai as today, with medium effort (use the SDK's effort/thinking control if available, else a fixed `max_tokens`; record what was used in `judge.effort`). Deterministic settings (temperature 0).
   - `calibrate_judge(cases) -> {calibrated: bool, results:[{case_key, expected, got}]}`: scores each golden's `golden_answer` (must return its `label`) and every `calibration_examples` entry (must return its label). Run once per `make eval` before scoring; stored on `eval_runs.totals.judge_calibration` and on each `eval_results.judge.calibrated`.
   - `deck_outline` is rendered by the runner from `artifact_manifest`: `slide N · <layout> · headline · kpi <kpi_label>=<kpi> · chart <chart_type> · table <rows> rows`.
4. **Pass rule.** `passed = g3_format.passed and (g1.score is None or g1.score >= 0.8) and (g5 is None or g5.passed)`. The judge label is recorded, displayed, and **does not gate** while `len(cases) < HOLDOUT_MIN_CASES`. Keep a `JUDGE_GATES` constant computed from the pack size so the switch is one line when the pack reaches 10.
5. **UI.** GraderEditor: `golden_answer` textarea, `label` select, calibration examples list (label + answer), checkpoints editor (expected_skills chips, derived_cols, layouts_any_of, kpi_label_contains). Evals tab: judge label badge + diagnosis per case; calibration status per run. Remove every insight-score display.
6. Tests: pack round-trip; judge parsing/normalisation; calibration pass/fail paths (mock the model); pass rule unchanged by judge; checkpoints scoring on fixtures.

### W-D · M3 offline optimiser (Opus, high)

Files: new `scripts/skill_miner.py`, new `scripts/reflect.py`, new `evals/optimiser/` (prompts), `tests/`.

Both use the Claude Agent SDK (`claude-agent-sdk`, same auth as the data-agent: `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`), model `claude-opus-5`. Both are read-only against the DB (`ADMIN_RO_DATABASE_URL`) and the only write they perform is a git branch + draft PR via `gh pr create --draft` (D4). Neither ever modifies the current branch's working tree: they work in `git worktree add .worktrees/optimise-<slug>`.

1. **skill_miner.py** (`make skill-mine [RUN=<eval_run_id>]`): reads `skill_gaps` + `used_inline_math` from `app.query_runs` (last N runs or the eval run's cases) and `eval_results.judge.diagnosis = 'analysis'` + `checkpoints.analysis` failures; clusters by `need`; for the top cluster asks Opus to write one new `@skill` function in `agent/skills/analysis.py` (+ unit test) that would have served those runs; **tests the candidate** by executing each affected golden's `golden_sandbox` (or a synthesized call) through `agent.sandbox.run_code` against the golden's `golden_truth` rows and the `checkpoints.analysis.derived_cols`; only if the test passes, commit on `optimise/skill-<slug>` and open a draft PR whose body lists the runs and gaps it addresses. Otherwise print the failure and exit 1.
2. **reflect.py** (`make reflect RUN=<eval_run_id>`): for each failed or `judge.label = low` case, loads the trace (`trace.json` artifact from MLflow or `query_runs.trace`), the judge diagnosis, and the checkpoint scores; asks Opus for **one** diff to either `agent/prompts/workspace_claude.md` or one `knowledge/**/*.md` page (never both, never code), with a one-paragraph hypothesis; opens a draft PR `optimise/reflect-<slug>` with the hypothesis in the body and the affected case keys.
3. Both print a JSON summary to stdout (`{pr_url, branch, files, hypothesis}`) for M5's log.
4. Tests: clustering; candidate-test harness with a fixture skill that passes and one that fails (no LLM call — mock the SDK); PR body rendering.

### W-E · M4 knowledge curator path (Sonnet, medium)

Files: `agent/knowledge.py`, `agent/workspace.py` (loader), `agent/main.py` (admin endpoints), `services/backend-api/app/routers/` (proxy `/admin/knowledge`), new `scripts/knowledge_pack.py` (`make knowledge-export` / `knowledge-import`), `frontend/src/features/architecture/ArchitecturePage.tsx` (edit box on knowledge pages), `frontend/src/lib/api.ts`, `tests/`.

1. `knowledge.py`: `load_overrides()` with a 5 s TTL cache (mirror `ordinals.load_overrides`), keyed by page path; `pages()` prefers the DB body when present; `knowledge_version()` hashes files **plus** DB overrides (so a curator edit changes the fingerprint) — keep `lru_cache` semantics by keying on the overrides snapshot hash.
2. Endpoints on data-agent: `GET /agent/knowledge` (list, with `source: file|db`, version), `GET /agent/knowledge/{path}`, `PUT /agent/knowledge/{path}` `{body, author}` → upsert + log row + bump version. Backend-api proxies under `/admin/knowledge*` (admin only, same pattern as `/admin/pack`).
3. `scripts/knowledge_pack.py export` writes every DB override back to `services/data-agent/knowledge/<path>` and prints the files changed (for a commit); `import` seeds DB rows from files (idempotent, only when the DB has no row).
4. Architecture tab: knowledge page view gains an edit textarea + Save (author = current user), showing `version` and `source`.
5. Tests: override precedence; version hash changes on edit; export round-trip.

### W-F · M5 acceptance (coordinator, after A–E)

Author `evals/cases/s49_test.yaml` with **two made-up goldens** on the live marts (one scalar T1 on nsw_rent, one ranked_set T2 on nsw_sales), each with `golden_answer`, `label: high`, two calibration examples (medium, low), and full checkpoints. Then run and record in this file's "M5 run log" section:

1. `make eval CASE=…` ×3 on both → baseline; judge calibrated; ≥ 6 child spans per run in MLflow; `eval_results` rows carry trace + MLflow ids + judge + checkpoints.
2. `make register` → bundle artifacts present.
3. Edit one helper in `skills/analysis.py` (a real, harmless improvement) → `make register` mints a new fingerprint → set `@challenger`.
4. `make eval` for the challenger → `make promote` → PASS or HOLD with the verdict JSON.
5. `make agent-checkout FP=<old champion>` → the old `skills/analysis.py` is reproduced byte-for-byte.
6. `make skill-mine RUN=<id>` and `make reflect RUN=<id>` → two draft PR URLs.
7. Curator edit of one knowledge page through the UI → `make knowledge-export` → fingerprint changes.

## Make targets (added by the coordinator)

```
agent-checkout FP=av-…      knowledge-export      knowledge-import
skill-mine [RUN=…]          reflect RUN=…         eval-calibrate
```

## Conventions

- Python: `uv run ruff format . && uv run ruff check . --fix && uv run mypy` clean; tests from `services/data-agent` (`cd services/data-agent && uv run pytest -q`) and `services/backend-api`.
- Frontend: `npm run typecheck` (tsc) clean in `frontend/`.
- No new secrets. Never commit `.env`. Prod is demo mode and has no data-agent; nothing here touches prod.
- Do not touch `migrations/` (coordinator-owned) or `Makefile` (coordinator-owned). If you need a column or target that is missing, say so in your report.

## M5 run log

(filled in by W-F)
