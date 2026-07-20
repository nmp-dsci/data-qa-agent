# Cycle 001 — extraction grain on trend questions

**Pack** `pv-96c5b5e8` (2 cases) · **lever** knowledge · **gate** PASS

| | base | candidate |
|---|---|---|
| build | `av-db1dc022a57e` | `av-fcb3cda38d4b` |
| knowledge | `kv-c7207240` | `kv-46608fe3` |
| prompt / skills | `p-f7435f83` / `s-d3c3a7db` | unchanged |
| run | `55528752-f09c-41b5-907a-04af15c8064b` | `f5d877b3-99cf-4f82-9e8f-a69155fa5818` |

## Diagnose

Baseline scored **0/2**, G1 mean 0.34. Reading the traces via the `query_runs`
audit record:

- `run_analysis` was called ~6× per question — the agent iterated its sandbox
  script rather than planning it.
- `describe_table` ~3× per question, re-reading marts already documented in the
  knowledge tree.
- The sales case answered "growth" as first-vs-last endpoints keyed on
  **postcode**, when the question implies a monthly series by **suburb**. It
  never produced the series the golden specifies, so G1 was 0.0.
- The rent case returned the mart's own grain (postcode × property_type ×
  bedroom_band × month) rather than the question's (month × postcode).

Cluster: **extraction grain on trend questions** — the agent does not have a
canonical shape for "how has X moved over time", so it improvises one per
question.

## Intervene — one lever

Added `knowledge/presentation/one-pass-trend-analysis.md`: the three-part shape
of a trend answer (one wide extract → derived rate series per group → objects),
the rule that a rate is `sum(numerator)/sum(denominator)` not a stored column,
and the instruction to aggregate at the *question's* grain rather than the
mart's.

No prompt or skill change — `prompt_hash` and `skills_hash` are identical across
the two builds, so the improvement is attributable to this file.

## Result

| metric | base | candidate | |
|---|---|---|---|
| pass rate | 0.0 | **1.0** | ▲ +1.0 |
| G1 extraction | 0.3407 | **1.0** | ▲ +0.66 |
| G4 turns | 19.0 | 24.0 | ▼ +5.0 |
| G4 latency | 42.3s | 41.2s | ▲ −1.1s |

**FIXED** both cases. **Regressions: 0 → gate PASS.**

Turns went *up*: the agent now does more work because it is producing the full
series it previously skipped. That is a real cost, accepted here because
correctness was the target; cycle 003 takes the cost back.

## Caveat

2 cases is below the holdout threshold, so this is **not** evidence the change
generalises — only that it fixed these two. The run records
`generalisation: unproven` for exactly this reason.

## A spec bug found on the way

The first attempt at this cycle ran against pack `pv-3088c3c8` and *appeared* to
regress: the knowledge page made the agent return `avg_weekly_rent` (correct)
while the grader was pinned to `total_weekly_rent` (the column the original
chat-promoted answer happened to emit). G1 fell to 0.0 on a **better** answer.

The grader was measuring the wrong thing, so it was fixed — a `ratio` aggregate
that reconstructs the rate from numerator and denominator on whichever side
supplies them. Because that edits the pack, `pack_version` changed, and
`eval_compare.py` **refuses to compare across packs**. The improvement therefore
had to be re-baselined and re-earned rather than laundered across the boundary.
That refusal is the structural defence against eval-tuning theatre.
