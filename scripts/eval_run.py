#!/usr/bin/env python3
"""Score the golden pack against the running agent (s24 M2).

This is the loop's engine. For each golden it replays the question through the
*real* ``/ask`` path — same guardrails, same RLS, same sandbox — as the user the
golden names, then grades the answer against the golden and persists the scores
with the exact build that produced them.

Deliberately works at N=1. A loop you cannot run until you have eighty cases is
a loop you will never start, so every filter narrows to a single case if you
want it to, and the run reports honestly what a corpus that small can and cannot
prove.

Usage (from the repo root; the DB is reached via `docker compose exec db`):
    uv run python scripts/eval_run.py                          # whole pack, baseline
    uv run python scripts/eval_run.py --dataset nsw_rent
    uv run python scripts/eval_run.py --case nsw_rent-give-...
    uv run python scripts/eval_run.py --experiment kb-yield-method \
        --hypothesis "annualising rent over median price fixes T2 yield cases"
    uv run python scripts/eval_run.py --no-judge               # skip the LLM half of G3
    uv run python scripts/eval_run.py --include-drafts         # also score draft goldens

An ``--experiment`` run records its id and links to the most recent baseline, so
the Evaluations tab can render it as base vs experiment.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yaml  # noqa: E402
from eval_pack import CASES_DIR, REPO_ROOT, pack_version  # noqa: E402

API = "http://localhost:8000"
AGENT = "http://localhost:8100"

# A full insight answer legitimately runs many tool round-trips.
ASK_TIMEOUT = 300
# Below this many scored cases a holdout slice is meaningless, so the run is
# labelled rather than pretending the result generalises.
HOLDOUT_MIN_CASES = 10


def _http(url: str, *, body: Any = None, token: str = "", timeout: int = 60) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Every replay is auditable as an eval, never confused with real usage.
    headers["X-Client-Channel"] = "eval"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _psql(query: str, service: str = "db") -> str:
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            service,
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


def _scalar(query: str) -> str:
    """First line of a query's output.

    ``INSERT ... RETURNING id`` prints the id *and* a trailing ``INSERT 0 1``
    status line, so taking the whole stdout hands the next statement a malformed
    uuid.
    """
    out = _psql(query)
    return out.splitlines()[0].strip() if out else ""


def _lit(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value).replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def load_cases(
    dataset: str | None,
    tier: str | None,
    case_key: str | None,
    include_drafts: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Read the pack from disk — the repo is the source of truth, not the DB.

    A ``draft`` golden has no reviewed golden_sql/grader yet, so replaying it
    scores the agent against empty ground truth and looks like a failure. Drafts
    are therefore skipped unless ``--include-drafts`` is passed (or the case is
    named explicitly via ``--case``, where the intent is unambiguous).

    Returns the matching cases plus the count of cases that passed the filters
    but were skipped only for being drafts, so the caller can say so instead of
    reporting "no cases matched" when the pack slice is all drafts.
    """
    if not CASES_DIR.is_dir():
        sys.exit("no pack at evals/cases — run `make eval-export` first")
    cases: list[dict[str, Any]] = []
    drafts_skipped = 0
    for path in sorted(CASES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in doc.get("cases") or []:
            if dataset and case.get("dataset") != dataset:
                continue
            if tier and case.get("tier") != tier:
                continue
            if case_key and case.get("case_key") != case_key:
                continue
            if not include_drafts and not case_key and case.get("authoring_status") == "draft":
                drafts_skipped += 1
                continue
            cases.append(case)
    return cases, drafts_skipped


def _rows_as_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise a {columns, rows} result into row dicts the graders expect."""
    cols = payload.get("columns") or []
    out: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)):
            # strict=False: a short row is padded out rather than raising —
            # a malformed result should score badly, not crash the run.
            out.append({str(c): v for c, v in zip(cols, row, strict=False)})
    return out


def _apply_composite_key(rows: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    """Add a synthetic ``_key`` joining several columns.

    The graders key a series on one column, but a real comparison question
    ("2077 vs 2076") returns several entities per month. Keyed on month alone the
    lookup map keeps only the last row per month and G1 becomes noise. Joining
    the identifying columns into one key makes each point comparable, without
    changing grader semantics.
    """
    out = []
    for row in rows:
        merged = dict(row)
        merged["_key"] = "|".join(str(row.get(f, "")) for f in fields)
        out.append(merged)
    return out


def _ratio(rows: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce each key to a *rate*, however the side happened to express it.

    A trend question ("how has rent moved") is about a rate, but the two sides
    rarely produce it the same way: the golden's SQL returns the numerator and
    denominator to sum, while a well-written agent answer often returns the
    computed average directly. Pinning the grader to one column name means a
    better answer scores zero — which is what happened on this pack's first
    experiment, and it was the *spec* that was wrong, not the agent.

    So: sum numerator and denominator when both are present (the correct way to
    average a rate across sub-segments), otherwise fall back to averaging a
    pre-computed value column.
    """
    value = str(spec.get("value") or "")
    num, den = str(spec.get("numerator") or ""), str(spec.get("denominator") or "")
    sums: dict[str, list[float]] = {}
    for row in rows:
        key = row["_key"]
        bucket = sums.setdefault(key, [0.0, 0.0, 0.0, 0.0])  # num, den, value_sum, n
        if num and den and row.get(num) is not None and row.get(den) is not None:
            try:
                bucket[0] += float(row[num] or 0)
                bucket[1] += float(row[den] or 0)
            except (TypeError, ValueError):
                pass
        if row.get(value) is not None:
            try:
                bucket[2] += float(row[value] or 0)
                bucket[3] += 1
            except (TypeError, ValueError):
                pass
    out = []
    for key, (n, d, vsum, count) in sums.items():
        if d:
            out.append({"_key": key, value: n / d})
        elif count:
            out.append({"_key": key, value: vsum / count})
    return out


def _aggregate(rows: list[dict[str, Any]], value: str) -> list[dict[str, Any]]:
    """Sum ``value`` per ``_key`` so both sides are compared at the same grain.

    A golden's SQL is often finer-grained than the question it answers — this
    pack's rent golden groups by property_type as well as month and postcode,
    while "rent trends for 2077 vs 2076" is a month-by-postcode question. Without
    rolling up, G1 compares one property type against a total and scores 0 for a
    correct answer. Rolling up compares like with like.
    """
    totals: dict[str, float] = {}
    for row in rows:
        try:
            totals[row["_key"]] = totals.get(row["_key"], 0.0) + float(row.get(value) or 0)
        except (TypeError, ValueError):
            continue
    return [{"_key": k, value: v} for k, v in totals.items()]


def _turns_for(run_id: str | None) -> int:
    """Agent turns for a run, read from the audit record.

    The ``/ask`` response only carries ``steps`` for admins, but a golden replays
    as the user it names — so the trace has to come from ``app.query_runs``,
    which records it regardless of who asked.
    """
    if not run_id:
        return 0
    out = _scalar(
        "SELECT coalesce(jsonb_array_length(trace), 0) FROM app.query_runs "
        f"WHERE id = {_lit(run_id)}::uuid"
    )
    return int(out) if out.isdigit() else 0


def golden_truth(case: dict[str, Any], token: str) -> list[dict[str, Any]]:
    """Ground truth = what ``golden_sql`` returns *now*, under the golden's user.

    Recomputed rather than read from the pack on purpose: G1 grades values, and
    pinning stale values would grade the agent against a snapshot of a mart that
    may no longer exist. Drift in the underlying data is caught by the pack-lint
    gate, not by silently scoring against yesterday's numbers.
    """
    sql = case.get("golden_sql")
    if not sql:
        return []
    try:
        result = _http(f"{API}/sql", body={"sql": sql}, token=token, timeout=120)
    except urllib.error.HTTPError as exc:
        print(f"    ! golden_sql failed: {exc.code} {exc.read()[:160]!r}")
        return []
    return _rows_as_dicts(result)


def score_case(case: dict[str, Any], *, use_judge: bool) -> dict[str, Any]:
    """Replay one golden and grade the answer. Never raises — a failure is a score."""
    key = case.get("case_key", "?")
    started = time.time()
    as_user = case.get("as_user") or "user1"
    print(f"  {key}")

    try:
        token = _http(f"{API}/auth/dev-login", body={"username": as_user})["access_token"]
    except Exception as exc:  # noqa: BLE001
        return {"case_key": key, "passed": False, "error": f"login failed: {exc}"}

    try:
        answer = _http(
            f"{API}/ask", body={"question": case["question"]}, token=token, timeout=ASK_TIMEOUT
        )
    except Exception as exc:  # noqa: BLE001 - an agent failure is the result
        return {
            "case_key": key,
            "passed": False,
            "error": f"ask failed: {exc}",
            "latency_ms": int((time.time() - started) * 1000),
        }

    latency_ms = int((time.time() - started) * 1000)
    golden_rows = golden_truth(case, token)
    actual_rows = _rows_as_dicts(answer)

    spec = case.get("grader") or {}
    key_fields = list(spec.get("key_fields") or [])
    if key_fields:
        golden_rows = _apply_composite_key(golden_rows, key_fields)
        actual_rows = _apply_composite_key(actual_rows, key_fields)
        if spec.get("aggregate") == "ratio":
            golden_rows = _ratio(golden_rows, spec)
            actual_rows = _ratio(actual_rows, spec)
        elif spec.get("aggregate") == "sum" and spec.get("value"):
            golden_rows = _aggregate(golden_rows, str(spec["value"]))
            actual_rows = _aggregate(actual_rows, str(spec["value"]))

    try:
        graded = _http(
            f"{AGENT}/agent/eval/grade",
            body={
                "question": case["question"],
                "grader": spec,
                "golden_rows": golden_rows,
                "actual_rows": actual_rows,
                "report": answer.get("report"),
                "answer": answer.get("answer", ""),
                "judge": use_judge,
            },
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "case_key": key,
            "passed": False,
            "error": f"grading failed: {exc}",
            "latency_ms": latency_ms,
        }

    g1 = graded.get("g1") or {}
    g3_format = graded.get("g3_format") or {}
    g3_insight = graded.get("g3_insight") or {}

    # G4 — ops. Turns is the headline cost metric: it is what actually drives
    # billed tokens on this stack, and it is the number an intervention can move.
    g4 = {
        "turns": _turns_for(answer.get("run_id")),
        "latency_ms": latency_ms,
        "input_tokens": answer.get("input_tokens"),
        "output_tokens": answer.get("output_tokens"),
        "row_count": answer.get("row_count", 0),
    }

    # G2 — preparation: did the sandbox actually build the objects the golden
    # specifies? Graded structurally here; the values themselves are G1's job.
    wanted = {o.get("name") for o in (case.get("golden_objects") or []) if isinstance(o, dict)}
    built = set(g3_format.get("object_types") or [])
    g2 = {
        "expected_objects": sorted(x for x in wanted if x),
        "built_object_types": sorted(built),
        "score": 1.0 if not wanted else round(len(built) / max(len(wanted), 1), 4),
    }

    # A case passes when the numbers are right and the report is well-formed.
    # Insight is scored and reported but does not gate on its own — a judge is
    # advisory until it has been calibrated against human ratings.
    g1_score = g1.get("score")
    passed = bool(g3_format.get("passed")) and (g1_score is None or g1_score >= 0.8)

    return {
        "case_key": key,
        "tier": case.get("tier"),
        "dataset": case.get("dataset"),
        "holdout": bool(case.get("holdout")),
        "query_run_id": answer.get("run_id"),
        "g1": g1,
        "g2": g2,
        "g3": {"format": g3_format, "insight": g3_insight},
        "g4": g4,
        "passed": passed,
        "latency_ms": latency_ms,
    }


def persist(
    results: list[dict[str, Any]], *, args: argparse.Namespace, pack_v: str, totals: dict[str, Any]
) -> str:
    """Write the run and its per-case results, stamped with the build under test."""
    # The build that actually answered, taken from the runs this eval produced —
    # not "the newest agent_versions row". Those differ the moment the agent is
    # redeployed mid-session, which is exactly what an experiment does, and the
    # wrong one silently attributes an experiment's results to the baseline build
    # (the compare then reports "identical build", hiding the very lever the run
    # was designed to isolate).
    run_ids = [r.get("query_run_id") for r in results if r.get("query_run_id")]
    version_id = None
    if run_ids:
        ids = ", ".join(f"{_lit(rid)}::uuid" for rid in run_ids)
        version_id = (
            _scalar(
                f"SELECT agent_version_id FROM app.query_runs WHERE id IN ({ids}) "
                "AND agent_version_id IS NOT NULL LIMIT 1"
            )
            or None
        )
    base_run = getattr(args, "base", None)
    if args.experiment and not base_run:
        # The run this attempt is actually arguing against: the most recent run
        # *on the same pack*, whichever it was. Defaulting to "the newest run
        # with no experiment_id" picked the original baseline forever, so a
        # second experiment was scored against a pre-improvement state — nothing
        # looked like a regression and the gate reported PASS on a run that had
        # visibly broken a case. Same pack, because a different pack is not a
        # comparable measurement (eval_compare refuses those outright).
        base_run = (
            _scalar(
                "SELECT id FROM app.eval_runs WHERE pack_version = "
                f"{_lit(pack_v)} ORDER BY started_at DESC LIMIT 1"
            )
            or None
        )

    judge_m = ""
    for r in results:
        verdict = (r.get("g3") or {}).get("insight") or {}
        judge_m = verdict.get("judge_model") or judge_m
    judge_hash = ""
    for r in results:
        verdict = (r.get("g3") or {}).get("insight") or {}
        judge_hash = verdict.get("judge_prompt_hash") or judge_hash

    run_id = _scalar(
        "INSERT INTO app.eval_runs (agent_version_id, dataset, pack, pack_version, "
        "judge_model, judge_prompt_hash, totals, experiment_id, hypothesis, base_run_id, "
        "finished_at) VALUES ("
        f"{_lit(version_id)}::uuid, {_lit(args.dataset or 'all')}, 'nsw_property', "
        f"{_lit(pack_v)}, {_lit(judge_m)}, {_lit(judge_hash)}, {_lit(totals)}::jsonb, "
        f"{_lit(args.experiment)}, {_lit(args.hypothesis)}, {_lit(base_run)}::uuid, now()"
        ") RETURNING id"
    )

    statements = []
    for r in results:
        statements.append(
            "INSERT INTO app.eval_results (eval_run_id, case_id, query_run_id, tier, "
            "g1, g2, g3, g4, passed, notes) SELECT "
            f"{_lit(run_id)}::uuid, c.id, {_lit(r.get('query_run_id'))}::uuid, "
            f"{_lit(r.get('tier'))}, {_lit(r.get('g1') or {})}::jsonb, "
            f"{_lit(r.get('g2') or {})}::jsonb, {_lit(r.get('g3') or {})}::jsonb, "
            f"{_lit(r.get('g4') or {})}::jsonb, {_lit(bool(r.get('passed')))}, "
            f"{_lit(r.get('error') or '')} "
            f"FROM app.eval_cases c WHERE c.case_key = {_lit(r['case_key'])};"
        )
    if statements:
        _psql("BEGIN; " + " ".join(statements) + " COMMIT;")
    return run_id


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in results if not r.get("error")]
    passed = [r for r in scored if r.get("passed")]
    g1s = [
        r["g1"]["score"]
        for r in scored
        if isinstance((r.get("g1") or {}).get("score"), (int, float))
    ]
    insights = [
        (r.get("g3") or {}).get("insight", {}).get("total")
        for r in scored
        if isinstance((r.get("g3") or {}).get("insight", {}).get("total"), (int, float))
    ]
    turns = [r["g4"]["turns"] for r in scored if r.get("g4")]
    return {
        "cases": len(results),
        "errors": len(results) - len(scored),
        "passed": len(passed),
        "pass_rate": round(len(passed) / len(results), 4) if results else 0.0,
        "g1_mean": round(sum(g1s) / len(g1s), 4) if g1s else None,
        "g3_insight_mean": round(sum(insights) / len(insights), 2) if insights else None,
        "g4_turns_mean": round(sum(turns) / len(turns), 2) if turns else None,
        # Honest about what a small corpus can prove.
        "generalisation": "unproven" if len(results) < HOLDOUT_MIN_CASES else "holdout-scored",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dataset", help="only this dataset slug")
    parser.add_argument("--tier", help="only this tier (T1..T7)")
    parser.add_argument("--case", dest="case_key", help="only this case_key")
    parser.add_argument(
        "--experiment", default=None, help="label this run as an improvement attempt"
    )
    parser.add_argument("--hypothesis", default=None, help="what this attempt expects to fix")
    parser.add_argument(
        "--base",
        default=None,
        help="eval_run id this attempt argues against (default: newest run on the same pack)",
    )
    parser.add_argument("--no-judge", action="store_true", help="skip the LLM half of G3")
    parser.add_argument(
        "--include-drafts",
        action="store_true",
        help="also score draft goldens (skipped by default — no reviewed benchmark)",
    )
    args = parser.parse_args()

    cases, drafts_skipped = load_cases(
        args.dataset, args.tier, args.case_key, include_drafts=args.include_drafts
    )
    if not cases:
        msg = "no cases matched the filters"
        if drafts_skipped:
            msg += (
                f" ({drafts_skipped} draft golden(s) skipped — pass --include-drafts to score them)"
            )
        sys.exit(msg)

    pack_v = pack_version()
    label = f"experiment {args.experiment}" if args.experiment else "baseline"
    print(f"eval · {len(cases)} case(s) · pack {pack_v} · {label}")

    results = [score_case(c, use_judge=not args.no_judge) for c in cases]
    totals = summarise(results)
    run_id = persist(results, args=args, pack_v=pack_v, totals=totals)

    print(f"\nrun {run_id}")
    for r in results:
        if r.get("error"):
            print(f"  ERROR {r['case_key']}: {r['error']}")
            continue
        g1 = (r.get("g1") or {}).get("score")
        insight = (r.get("g3") or {}).get("insight", {}).get("total")
        mark = "PASS" if r.get("passed") else "FAIL"
        print(f"  {mark} {r['case_key']}  G1={g1}  insight={insight}  turns={r['g4']['turns']}")
    print(f"\n{json.dumps(totals, indent=2)}")
    if totals["generalisation"] == "unproven":
        print(
            f"\nnote: fewer than {HOLDOUT_MIN_CASES} cases — no holdout slice, "
            "so an improvement here is not yet evidence that it generalises."
        )


if __name__ == "__main__":
    main()
