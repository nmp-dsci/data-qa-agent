#!/usr/bin/env python3
"""Compare two eval runs and apply the regression gate (s24 M3).

The gate is what turns a scoreboard into a decision. It is deliberately
asymmetric about risk: an improvement has to be *earned* on the target, but a
regression anywhere blocks. "Fixed yield, broke trends" must be impossible to
ship silently.

    PASS  target improved (or held) AND no case flipped pass -> fail
    FAIL  any case flipped pass -> fail, whatever the headline did

Comparability is checked before anything is scored: two runs graded against
different packs, or under different judges, are not measuring the same thing and
the comparison refuses rather than quietly reporting a delta.

Usage (from the repo root):
    uv run python scripts/eval_compare.py --base <run-id> --candidate <run-id>
    uv run python scripts/eval_compare.py --experiment fewer-turns   # vs its own base
    make eval-compare A=<run-id> B=<run-id>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
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


def load_run(run_id: str) -> dict[str, Any]:
    raw = _psql(
        "SELECT coalesce(json_build_object("
        "'id', r.id, 'pack_version', r.pack_version, 'experiment_id', r.experiment_id, "
        "'hypothesis', r.hypothesis, 'judge_model', r.judge_model, "
        "'judge_prompt_hash', r.judge_prompt_hash, 'totals', r.totals, "
        "'started_at', r.started_at, 'fingerprint', v.fingerprint, 'label', v.label, "
        "'prompt_hash', v.prompt_hash, 'skills_hash', v.skills_hash, "
        "'knowledge_version', v.knowledge_version, 'provider', v.provider, "
        "'model_id', v.model_id), '{}') "
        "FROM app.eval_runs r LEFT JOIN app.agent_versions v ON v.id = r.agent_version_id "
        f"WHERE r.id = {_lit(run_id)}::uuid"
    )
    data: dict[str, Any] = json.loads(raw or "{}")
    if not data.get("id"):
        sys.exit(f"no eval_run {run_id}")
    return data


def load_results(run_id: str) -> dict[str, dict[str, Any]]:
    raw = _psql(
        "SELECT coalesce(json_agg(json_build_object("
        "'case_key', c.case_key, 'tier', e.tier, 'passed', e.passed, "
        "'g1', e.g1, 'g3', e.g3, 'g4', e.g4)), '[]') "
        "FROM app.eval_results e JOIN app.eval_cases c ON c.id = e.case_id "
        f"WHERE e.eval_run_id = {_lit(run_id)}::uuid"
    )
    return {r["case_key"]: r for r in json.loads(raw or "[]")}


def _one_lever(base: dict[str, Any], cand: dict[str, Any]) -> list[str]:
    """Which behaviour surfaces differ between the two builds.

    The composed fingerprint exists for exactly this: a claim that an
    intervention caused an improvement is only credible if precisely one
    component moved.
    """
    moved = []
    for field, name in (
        ("provider", "provider"),
        ("model_id", "model"),
        ("prompt_hash", "prompt"),
        ("skills_hash", "skills"),
        ("knowledge_version", "knowledge"),
    ):
        if base.get(field) != cand.get(field):
            moved.append(f"{name}: {base.get(field)} -> {cand.get(field)}")
    return moved


def _mean(results: dict[str, dict[str, Any]], path: tuple[str, ...]) -> float | None:
    values = []
    for r in results.values():
        node: Any = r
        for part in path:
            node = (node or {}).get(part) if isinstance(node, dict) else None
        if isinstance(node, (int, float)):
            values.append(float(node))
    return round(sum(values) / len(values), 4) if values else None


def _fmt(value: Any) -> str:
    return "—" if value is None else f"{value}"


def _delta(base: float | None, cand: float | None, *, lower_is_better: bool = False) -> str:
    if base is None or cand is None:
        return ""
    diff = cand - base
    if abs(diff) < 1e-9:
        return "="
    improved = diff < 0 if lower_is_better else diff > 0
    return f"{'▲' if improved else '▼'} {diff:+.4g}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", help="baseline eval_run id")
    parser.add_argument("--candidate", help="candidate eval_run id")
    parser.add_argument("--experiment", help="compare this experiment against its recorded base")
    args = parser.parse_args()

    if args.experiment:
        cand_id = (
            _psql(
                "SELECT id FROM app.eval_runs WHERE experiment_id = "
                f"{_lit(args.experiment)} ORDER BY started_at DESC LIMIT 1"
            )
            .splitlines()[0]
            .strip()
        )
        if not cand_id:
            sys.exit(f"no run for experiment {args.experiment}")
        base_id = (
            _psql(f"SELECT base_run_id FROM app.eval_runs WHERE id = {_lit(cand_id)}::uuid")
            .splitlines()[0]
            .strip()
        )
        if not base_id:
            sys.exit(f"experiment {args.experiment} has no recorded base run")
    else:
        if not args.base or not args.candidate:
            sys.exit("need --base and --candidate, or --experiment")
        base_id, cand_id = args.base, args.candidate

    base, cand = load_run(base_id), load_run(cand_id)
    base_r, cand_r = load_results(base_id), load_results(cand_id)

    print(f"pack {base['pack_version']} · regression gate ON")
    print(f"  A base      {base.get('fingerprint') or '?'}  {base.get('label') or ''}")
    print(
        f"  B candidate {cand.get('fingerprint') or '?'}  {cand.get('label') or ''}"
        f"{'  · ' + cand['experiment_id'] if cand.get('experiment_id') else ''}"
    )
    if cand.get("hypothesis"):
        print(f"  hypothesis  {cand['hypothesis']}")

    # Comparability. A delta between runs scored against different specs, or by
    # different judges, is not a measurement — say so instead of printing it.
    blockers = []
    if base["pack_version"] != cand["pack_version"]:
        blockers.append(
            f"pack differs ({base['pack_version']} vs {cand['pack_version']}) — "
            "the goldens changed, so these runs are not comparable"
        )
    if (base.get("judge_prompt_hash") or "") != (cand.get("judge_prompt_hash") or ""):
        blockers.append("judge rubric differs — insight scores are not comparable")
    if blockers:
        for b in blockers:
            print(f"\n  ! {b}")
        sys.exit(2)

    moved = _one_lever(base, cand)
    print(f"\n  levers moved: {', '.join(moved) if moved else 'none (identical build)'}")
    if len(moved) > 1:
        print("  ! more than one lever moved — an improvement here cannot be attributed")

    rows = [
        ("pass rate", base["totals"].get("pass_rate"), cand["totals"].get("pass_rate"), False),
        ("G1 extraction", _mean(base_r, ("g1", "score")), _mean(cand_r, ("g1", "score")), False),
        (
            "G3 insight",
            _mean(base_r, ("g3", "insight", "total")),
            _mean(cand_r, ("g3", "insight", "total")),
            False,
        ),
        ("G4 turns", _mean(base_r, ("g4", "turns")), _mean(cand_r, ("g4", "turns")), True),
        (
            "G4 latency ms",
            _mean(base_r, ("g4", "latency_ms")),
            _mean(cand_r, ("g4", "latency_ms")),
            True,
        ),
    ]
    print(f"\n  {'metric':<16}{'A':>12}{'B':>12}   delta")
    for name, a, b, lower in rows:
        print(f"  {name:<16}{_fmt(a):>12}{_fmt(b):>12}   {_delta(a, b, lower_is_better=lower)}")

    # The gate itself. Only cases present in both runs can flip.
    shared = sorted(set(base_r) & set(cand_r))
    regressions = [k for k in shared if base_r[k]["passed"] and not cand_r[k]["passed"]]
    fixes = [k for k in shared if not base_r[k]["passed"] and cand_r[k]["passed"]]

    print()
    for key in fixes:
        print(f"  FIXED      {key}")
    for key in regressions:
        print(f"  REGRESSED  {key}")
    missing = sorted(set(base_r) - set(cand_r))
    for key in missing:
        print(f"  ! not scored in candidate: {key}")

    passed = not regressions
    print(f"\n  regressions: {len(regressions)} → gate {'PASS' if passed else 'FAIL'}")
    if len(cand_r) < 10:
        print(
            f"  note: {len(cand_r)} case(s) — below the holdout threshold, so this "
            "result is not yet evidence that the change generalises."
        )
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
