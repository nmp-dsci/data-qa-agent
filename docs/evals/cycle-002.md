# Cycle 002 — turn cost via the system prompt (BLOCKED)

**Pack** `pv-96c5b5e8` (2 cases) · **lever** prompt · **gate** FAIL — not shipped

| | base | candidate |
|---|---|---|
| build | `av-fcb3cda38d4b` | `av-7d5ddb2bd1a9` |
| prompt | `p-f7435f83` | `p-eecbbf6e` |
| knowledge / skills | `kv-46608fe3` / `s-d3c3a7db` | unchanged |
| run | `f5d877b3-99cf-4f82-9e8f-a69155fa5818` | `99bd525b-c780-490a-b858-b6eae17301a7` |

## Diagnose

After cycle 001 both cases passed, but at **24 turns** and 41s per question.
Turns is the headline cost metric on this stack — it is what actually drives
billed tokens. The trace breakdown still showed `describe_table` re-reading marts
the agent had already seen.

## Intervene — one lever

Added a `TURN DISCIPLINE` block to the sandbox system prompt in
`agent/sandbox_agent.py`: describe each table at most once per question, skip it
entirely when a knowledge page covers the mart, and batch table discovery up
front.

Only `prompt_hash` moved.

## Result

| metric | base | candidate | |
|---|---|---|---|
| pass rate | 1.0 | 0.5 | ▼ −0.5 |
| G1 extraction | 1.0 | 0.8407 | ▼ −0.16 |
| G4 turns | 24.0 | **20.5** | ▲ −3.5 |
| G4 latency | 41.2s | 53.3s | ▼ +12.1s |

**REGRESSED** `nsw_rent-give-me-rent-trends-for-postcode-2077-vs-2076-2fb4`.

**Regressions: 1 → gate FAIL. Change reverted, not shipped.**

## Why it was rejected

The headline the intervention targeted did improve — turns fell 3.5. Judged on
its own stated goal this was a success, and without a gate it would have shipped.
It also made the rent case wrong: starved of schema reads, the agent guessed a
grain and lost extraction accuracy.

This is the "fixed yield, broke trends" failure the gate exists to prevent, and
the reason the gate is **not** a function of the headline metric. Latency also
rose despite fewer turns — a reminder that turns and wall-clock are not
interchangeable proxies for cost.

The lesson carried into cycle 003: the guidance was right, but a blanket
instruction in the system prompt applies to every question, including ones where
schema exploration is genuinely needed. Scoping it to the domain where it holds
was the fix.
