# s48 eval gate — 3-golden proof run

`eval_run 5e08ae3e-432e-4617-b539-ab3552a32249` · pack `pv-d6f9c5b5` · dataset `nsw_property`
(`nsw_rent` + `nsw_sales`) · agent build live on `presentation-handover` (Claude Agent SDK runtime,
`claude-sonnet-5`) · judge model `deepseek-chat` · deck export **on** (every case produced a real
Slides deck + Sheet).

Commands run:

```
uv run python scripts/eval_pack.py import          # seeded the 3rd golden, pack_version pv-d6f9c5b5
make eval-lint                                      # zero-LLM pack-lint, all cases pass (2 skips)
make eval                                            # scored the 3 ready goldens live
uv run python scripts/inspect_run.py <run-id>        # diagnosed the 2 failures
```

## Results

| case (shape) | tier | G1 | G3 format | G3 insight (judge, /10) | G5 artifact | turns | latency | tokens (in/out) | overall |
|---|---|---|---|---|---|---|---|---|---|
| `nsw_rent-which-postcodes-had-the-fastest-rent-growth-over-0980` (table/ranked_set) | T2 | **0.0** | pass | 6 | 3 slides, chart+table present, deck+sheet ok | 70 | 137.6s | 430,885 / 8,646 | **FAIL** |
| `nsw_rent-median-weekly-rent-2br-units-postcode-2077-latest-month-s48a1` (scalar) | T1 | **0.0** | pass | 5 | 3 slides, chart present, deck+sheet ok | 49 | 98.1s | 245,525 / 4,696 | **FAIL** |
| `nsw_sales-show-house-price-growth-for-hornsby-normanhurst-96c3` (series/chart) | T2 | **1.0** | pass | 2 | 3 slides, chart present, deck+sheet ok | 85 | 210.6s | 547,330 / 12,075 | **PASS** |

Pass rate 1/3 (0.33). G3-format (structural report linting) passed on all three; every deck had 3
slides (Cover + 2 content), well above `min_slides`, and every slide that needed one carried a chart
or table. G5 is not persisted to `app.eval_results` by `eval_run.py` (only G1–G4 are columns on
`eval_results`), so the artifact grade above is reconstructed from the deck/sheet URLs and the
`Slide N added (...)` trace lines, not read back from a stored score.

Decks/sheets:
- rent-growth ranking: https://docs.google.com/presentation/d/1Wfe-RKKXGRTzXG2FVxo1CYRiSolq-xmorTPH8VJ6gPc/edit · https://docs.google.com/spreadsheets/d/1UkmRP48mJ-NNUq9wBtFcMW-x8upDM929UD_CSYlQMEQ/edit
- 2077 median rent scalar: https://docs.google.com/presentation/d/1t4nbilnJZUNukCvwN_4OwZ3LyyTNwSXTM2RPhfhusiA/edit · https://docs.google.com/spreadsheets/d/18Y7l7MMMkPwTclF8goimDUf1x-Bhw9rn2gGvFVoPc0U/edit
- Hornsby/Normanhurst price growth: https://docs.google.com/presentation/d/1RymOaad64gT9JndZ4kU7W2RV39caeeVxH-8Hv9GsOyA/edit · https://docs.google.com/spreadsheets/d/1eEDb9fF0MliXF4zctbnShU9ZLWDMt6w-0xhqmgJztgw/edit

Layouts used (recorded, not asserted — the pack's non-goal §10): all three decks were
Cover → content → content. Rent-growth used Ranked Bars then Table; the scalar case used
KPI + Trend twice (see finding below); the sales case used Headline + Trend then KPI + Trend.

## Diagnosis of the 2 failures

**`nsw_rent-median-weekly-rent...` (G1=0.0, scalar).** Not a golden-authoring bug: `golden_sql` was
re-verified live before this write-up and returns exactly one row, `718.0`, matching what the agent
itself computed and stated in its final answer ("$718 ... May 2026, based on 72 bond lodgements" —
`inspect_run.py 7c694e76…`). The mismatch is in the eval harness's G1 input: `score_case()` grades
`actual_rows = _rows_as_dicts(answer)`, i.e. the top-level `columns`/`rows` on the `/ask` response,
which is the agent's **primary SQL extract** (Q1: the full 113-row month series for the postcode/
type/band, ordered ascending, needed to draw the trend chart) — not the scalar the agent ultimately
reported. `grade_scalar` then compares the golden's single value against the *first* row of that
113-row series (an early month), so it scores 0 regardless of whether the agent's headline is right.
This is a grader/harness limitation for `kind: scalar` against an agent that (correctly) queries a
full series for chart context, not a defect in this golden's SQL or the agent's answer. No golden or
agent-code change made, per instructions — flagged as a gate limitation below.

**`nsw_rent-which-postcodes-had-the-fastest-rent-growth...` (G1=0.0, ranked_set).** Both sides compute
a defensible but *different* rent-growth ranking: the golden uses 12-month windows (now vs. 1 year
prior) with an `n_rented >= 200` floor per window; the agent (`inspect_run.py ae1fb0ad…`) used 6-month
windows (now vs. 24–30 months prior) with `n_rented >= 10`. The looser floor lets thin, high-variance
regional postcodes (e.g. 2422, "+46.2%" off a small base) dominate the agent's top-5, which barely
overlaps the golden's higher-floor, longer-window top-5 — exactly the noise the golden's own comment
warns a low floor produces. The question is genuinely ambiguous about window length and the minimum
sample size, and no single SQL is "the" right ranking; this is an existing (pre-s48) golden, not one
authored in this pass, so it was left as-is and only diagnosed, per instructions.

## Candidate goldens / curation items (not fixed here)

- **Scalar-shape grading gap**: `kind: scalar` needs a harness-level reducer (e.g. "take the row whose
  order column is max/latest") before it can grade any question where the agent's primary extract is a
  time series rather than a single row — likely every scalar KPI question the agent naturally answers,
  since it always pulls context for the chart. Candidate: extend `eval_run.py`'s existing
  key_fields/aggregate reduction (the `ratio`/`sum` modes already built for `series`) with a `latest`
  mode usable by `scalar` goldens.
- **Self-correcting deck on the scalar case**: the agent added a KPI slide with a stale/guessed figure,
  then re-verified via a second SQL query and appended a "Correction: ..." slide rather than fixing the
  first one — the delivered deck has a visibly wrong then corrected slide. Worth a curated golden that
  pins "verify before presenting" behaviour once the eval can grade it (judge or a dedicated check).
- **Low insight score despite G1=1.0** on the sales case (judge: 2/10, "dumps raw Python variable names
  and tuples instead of stating the house price growth"). The numbers were right and the deck had a
  chart, but the in-chat text before the deck read like debug output rather than an answer. Candidate
  for a curated golden targeting judge-scored narrative quality on multi-entity comparison questions.
- **Ambiguous ranking methodology** (see diagnosis above): "fastest rent growth" has no canonical
  window/floor. Candidate: either tighten the question text in a future golden ("...over the last 2
  years using 12-month windows, postcodes with at least 200 rentals per window") so the grader has an
  unambiguous target, or move this shape to a judge-scored/qualitative grader instead of `ranked_set`.

## Nothing blocked

All 3 tasks completed against the live stack; `make eval-import` confirmed 3 `ready` goldens in
`app.eval_cases`; `make eval-lint` passed (21 checks, 2 skips — pre-existing, unrelated to this pack).
No prompt, skill, or agent code was changed.

## Harness fix: scalar-shape grading gap (post-write-up)

Fixed the gap flagged above under "Scalar-shape grading gap". `kind: scalar` now reduces the
agent's extract to one value by precedence (`eval_graders.reduce_scalar_actual`, recorded as
`scalar_source`): **1** `manifest_kpi` — the deck's own headline KPI (taking the *last* KPI slide,
since the agent sometimes appends a "Correction: ..." slide after re-verifying, as it did on this
exact case); **2** `key_match` — join the golden row's non-value columns (month/period/date, or
whatever else it carries) onto the matching actual row; **3** `last_row` — extracts are
chronological, so >1 rows takes the latest, flagged `reduced_from: n`; **4** `first_row` — the old
behaviour, kept as the final fallback. A grader can force one mode via `reduce:`; this golden's
grader block was left on default precedence, which already resolves it. `parse_scalar_number`
strips `$`, `,`, `%` and handles k/m/b suffixes so `"$718/wk"` parses as `718`.

Since `services/data-agent`'s image isn't rebuilt from source edits, the fix runs client-side in
`scripts/eval_run.py` (cross-imports `agent.eval_graders` the way `tests/test_report.py` reaches
`agent.report` — no container involved) and overrides the remote grader's `g1` for `kind: scalar`
cases only; every other kind is untouched and still scored by the running container.

Re-ran only `nsw_rent-median-weekly-rent-2br-units-postcode-2077-latest-month-s48a1` live
(`make eval CASE=...`, run `c1d28133-722c-46f3-8746-126aebb7a640`): **G1 = 1.0**
(`scalar_source: manifest_kpi`, matching the deck's corrected `$718/wk` slide). The case still
reports overall FAIL — G3-format now fails on `"summary is empty"` (this run's report used custom
keys `n`/`month`/`median` instead of a `summary` field) — a separate, pre-existing report-shape gap,
unrelated to and out of scope for this fix. Deck:
https://docs.google.com/presentation/d/1_mBGfbXg0sNtGY8P6G01gexAhY6Kyls_cC6OKYiI_tk/edit · Sheet:
https://docs.google.com/spreadsheets/d/1-EITcW0zUIh7KQlX2aC7vTXGTKzOroWjQRcNE-k8syQ/edit (2 content
slides + Cover, each carrying a chart — G5 shape looks correct, not persisted to `app.eval_results`
so not re-verified as a stored score, same limitation noted above).
