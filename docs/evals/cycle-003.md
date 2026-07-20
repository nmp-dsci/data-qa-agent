# Cycle 003 — turn cost, scoped to the domain that needs it

**Pack** `pv-96c5b5e8` (2 cases) · **lever** knowledge · **gate** PASS

| | base | candidate |
|---|---|---|
| build | `av-fcb3cda38d4b` | `av-81129ac80761` |
| knowledge | `kv-46608fe3` | `kv-2418a90f` |
| prompt / skills | `p-f7435f83` / `s-d3c3a7db` | unchanged |
| run | `f5d877b3-99cf-4f82-9e8f-a69155fa5818` | `4df7abad-b61d-4756-92c6-f1884cb44d0b` |

## Diagnose

Cycle 002 established that the turn-discipline *guidance* worked (turns fell) but
that delivering it through the system prompt cost accuracy, because a system
prompt applies to every question — including ones where the agent genuinely needs
to explore an unfamiliar schema.

Hypothesis: the same guidance, scoped to trend questions over documented marts,
keeps the saving without the collateral damage.

## Intervene — one lever

Moved the guidance out of the system prompt (reverted) and into
`knowledge/presentation/one-pass-trend-analysis.md`, which is only retrieved for
trend/comparison questions: describe a table at most once, prefer the knowledge
page over `describe_table` for documented marts, resolve named entities in the
`WHERE` clause rather than via separate `lookup_values` calls.

Only `knowledge_version` moved. `prompt_hash` is back to `p-f7435f83`, identical
to the cycle-001 candidate, so this is a clean single-lever comparison against it.

## Result

| metric | base | candidate | |
|---|---|---|---|
| pass rate | 1.0 | 1.0 | = |
| G1 extraction | 1.0 | 1.0 | = |
| G4 turns | 24.0 | **19.5** | ▲ −4.5 (−19%) |
| G4 latency | 41.2s | **22.2s** | ▲ −19.0s (−46%) |

**Regressions: 0 → gate PASS.**

A better outcome than cycle 002 on its own target: more turns saved (−4.5 vs
−3.5), latency nearly halved rather than degraded, and no accuracy cost.

## Net across the three cycles

Against the honest baseline `55528752`:

| metric | baseline | now | |
|---|---|---|---|
| pass rate | 0.0 | **1.0** | ▲ +1.0 |
| G1 extraction | 0.3407 | **1.0** | ▲ +0.66 |
| G4 turns | 19.0 | 19.5 | ▼ +0.5 |
| G4 latency | 42.3s | **22.2s** | ▲ −47% |

Both cases now pass, at essentially the baseline turn count and roughly half the
wall-clock. Two interventions shipped, one blocked by the gate.

## Caveat

2 cases, below the holdout threshold: `generalisation: unproven` on every run.
These cycles prove the **loop** works — diagnose, isolate one lever, rescore,
gate — not that the agent is 19% cheaper in general. Broadening the pack is what
converts these into a claim that generalises.
