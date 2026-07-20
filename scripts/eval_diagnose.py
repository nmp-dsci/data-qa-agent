#!/usr/bin/env python3
"""Read-only diagnosis over a scored eval run (s24 M6).

The first half of the teacher agent, and deliberately the only half for now
(decision D-3). It has **read access and nothing else**: it clusters failures,
pulls the traces behind them, and proposes one-lever hypotheses. A human picks
the lever and makes the change.

That ordering is the point. An agent that can both propose an intervention *and*
score it is optimising against its own scoreboard; the defence is that the gate
must exist and be trusted before write access is granted. Cycles 001-003 are how
that trust gets built, and cycle 002 — where the gate rejected a change that hit
its own stated target — is why it matters.

Everything printed is derived from `eval_results` joined to the `query_runs`
audit record, so a hypothesis can always be traced back to evidence.

Usage (from the repo root):
    uv run python scripts/eval_diagnose.py                 # the latest run
    uv run python scripts/eval_diagnose.py <eval-run-id>
    uv run python scripts/eval_diagnose.py --failures-only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def _psql(query: str) -> str:
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "postgres",
            "-d",
            "dataqa",
            "-tA",
            "-c",
            query,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _lit(value: Any) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def _json(query: str) -> Any:
    return json.loads(_psql(query) or "null")


def latest_run() -> str:
    run = _psql("SELECT id FROM app.eval_runs ORDER BY started_at DESC LIMIT 1")
    if not run:
        sys.exit("no eval runs — score the pack with `make eval` first")
    return run.splitlines()[0].strip()


def load(run_id: str) -> dict[str, Any]:
    run = _json(
        "SELECT coalesce(json_build_object('id', r.id, 'experiment_id', r.experiment_id, "
        "'hypothesis', r.hypothesis, 'pack_version', r.pack_version, 'totals', r.totals, "
        "'label', v.label), 'null') FROM app.eval_runs r "
        "LEFT JOIN app.agent_versions v ON v.id = r.agent_version_id "
        f"WHERE r.id = {_lit(run_id)}::uuid"
    )
    if not run:
        sys.exit(f"no eval_run {run_id}")
    results = _json(
        "SELECT coalesce(json_agg(json_build_object('case_key', c.case_key, "
        "'question', c.question, 'dataset', c.dataset, 'tier', e.tier, "
        "'tags', c.tags, 'passed', e.passed, 'notes', e.notes, 'g1', e.g1, "
        "'g3', e.g3, 'g4', e.g4, 'query_run_id', e.query_run_id)), '[]') "
        "FROM app.eval_results e JOIN app.eval_cases c ON c.id = e.case_id "
        f"WHERE e.eval_run_id = {_lit(run_id)}::uuid"
    )
    return {"run": run, "results": results or []}


def tool_histogram(query_run_ids: list[str]) -> Counter[str]:
    """What the agent actually did, from the audit trace.

    The single most useful diagnostic here: a tool called many times per question
    is nearly always the cluster worth attacking, and it is invisible from the
    scores alone.
    """
    ids = [i for i in query_run_ids if i]
    if not ids:
        return Counter()
    joined = ", ".join(f"{_lit(i)}::uuid" for i in ids)
    rows = _json(
        "SELECT coalesce(json_agg(x), '[]') FROM ("
        "  SELECT coalesce(t->>'tool', t->>'kind') AS tool, count(*) AS n"
        "  FROM app.query_runs q, jsonb_array_elements(q.trace) t"
        f"  WHERE q.id IN ({joined}) GROUP BY 1) x"
    )
    return Counter({r["tool"]: r["n"] for r in rows or []})


def hypotheses(data: dict[str, Any], tools: Counter[str], n_cases: int) -> list[str]:
    """One-lever proposals, each tied to the evidence that motivated it.

    Deliberately conservative and few: the discipline is one change per cycle, so
    a long list of vague suggestions would be worse than a short list of specific
    ones. A human still chooses.
    """
    out: list[str] = []
    results = data["results"]
    failed = [r for r in results if not r.get("passed")]

    # G1 failures cluster on extraction: the numbers are wrong or absent.
    g1_bad = [
        r
        for r in failed
        if isinstance((r.get("g1") or {}).get("score"), (int, float)) and r["g1"]["score"] < 0.8
    ]
    if g1_bad:
        keys = ", ".join(r["case_key"] for r in g1_bad[:3])
        out.append(
            f"[knowledge] {len(g1_bad)} case(s) fail G1 extraction ({keys}). The agent "
            "is producing the wrong values or the wrong grain. A domain knowledge page "
            "stating the canonical extract shape for this question type is the "
            "cheapest lever — it is scoped to the questions that need it, unlike a "
            "system-prompt change."
        )

    # Structural G3 failures are a presentation problem, not a data problem.
    struct_bad = [r for r in failed if (r.get("g3") or {}).get("format", {}).get("issues")]
    if struct_bad:
        issues = Counter(
            issue
            for r in struct_bad
            for issue in (r.get("g3") or {}).get("format", {}).get("issues", [])
        )
        top = ", ".join(f"{k} (x{v})" for k, v in issues.most_common(3))
        out.append(
            f"[skills/presentation] {len(struct_bad)} case(s) fail G3 structure: {top}. "
            "The numbers may be right — this is the report shape. Check the skill that "
            "builds the missing object before touching extraction."
        )

    # Turn cost: the headline cost metric on this stack.
    if n_cases:
        per_case = {t: round(n / n_cases, 1) for t, n in tools.items()}
        repeated = {
            t: n for t, n in per_case.items() if n >= 2 and t not in {"model", "tool_return"}
        }
        if repeated:
            top = ", ".join(
                f"{t} x{n}/question" for t, n in sorted(repeated.items(), key=lambda kv: -kv[1])[:3]
            )
            out.append(
                f"[knowledge] Repeated tool use: {top}. Each repeat is a full model "
                "round-trip. If the repeated tool is schema exploration over a mart "
                "that a knowledge page already documents, say so in that page — note "
                "that the same instruction in the system prompt applies to every "
                "question and cost accuracy when tried (docs/evals/cycle-002.md)."
            )

    errors = [r for r in results if r.get("notes")]
    if errors:
        out.append(
            f"[infrastructure] {len(errors)} case(s) errored rather than scoring "
            f"({errors[0]['notes'][:90]}). Fix this before reading any score — an "
            "errored case is not a low score, it is no measurement at all."
        )

    if not out:
        out.append(
            "No failure cluster found: every case passed and no tool is being called "
            "repeatedly. The next lever is corpus breadth, not agent behaviour — a "
            "pack this small cannot show you what is still broken."
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_id", nargs="?", help="eval_run id (default: latest)")
    parser.add_argument("--failures-only", action="store_true", help="hide passing cases")
    args = parser.parse_args()

    run_id = args.run_id or latest_run()
    data = load(run_id)
    run, results = data["run"], data["results"]
    totals = run.get("totals") or {}

    print(f"run {run_id}")
    print(
        f"  {run.get('experiment_id') or 'baseline'} · pack {run.get('pack_version')} "
        f"· {run.get('label') or 'unstamped build'}"
    )
    if run.get("hypothesis"):
        print(f"  hypothesis: {run['hypothesis']}")
    print(
        f"  {totals.get('passed', 0)}/{totals.get('cases', 0)} pass · "
        f"G1 {totals.get('g1_mean')} · turns {totals.get('g4_turns_mean')}"
    )

    print("\ncases")
    for r in sorted(results, key=lambda x: (bool(x.get("passed")), x["case_key"])):
        if args.failures_only and r.get("passed"):
            continue
        mark = "PASS" if r.get("passed") else "FAIL"
        g1 = (r.get("g1") or {}).get("score")
        print(f"  {mark}  {r['case_key']}  [{r.get('tier')}/{r.get('dataset')}]  G1={g1}")
        for issue in (r.get("g3") or {}).get("format", {}).get("issues", [])[:3]:
            print(f"          structure: {issue}")
        if r.get("notes"):
            print(f"          error: {r['notes'][:120]}")

    tools = tool_histogram([r.get("query_run_id") for r in results])
    n = len(results) or 1
    if tools:
        print("\nwhat the agent did (per question)")
        for tool, count in tools.most_common(8):
            print(f"  {tool:<20} {count / n:>5.1f}")

    print("\nhypotheses — one lever each, a human chooses")
    for i, h in enumerate(hypotheses(data, tools, n), 1):
        print(f"  {i}. {h}")

    if (totals.get("cases") or 0) < 10:
        print(
            "\nnote: fewer than 10 cases. Treat every hypothesis as provisional — "
            "a cluster of one is an anecdote."
        )


if __name__ == "__main__":
    main()
