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
import os
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


def _host_port(name: str, default: str) -> str:
    """A compose host-port override: shell env first, then the repo .env.

    Compose reads .env itself, but this script runs on the host, so a machine
    that remapped a port (API_HOST_PORT=8010 here) needs the same value or
    every request lands on the wrong service.
    """
    if os.environ.get(name):
        return os.environ[name]
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip() or default
    return default


API = f"http://localhost:{_host_port('API_HOST_PORT', '8000')}"
AGENT = f"http://localhost:{_host_port('AGENT_HOST_PORT', '8100')}"

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
    # s44 M3b: the raw answer, kept only for the opt-in MLflow per-case capture
    # below (never persisted to app.eval_results — persist() reads a fixed set
    # of keys and ignores this one).
    raw_answer = {
        "answer": answer.get("answer", ""),
        "report": answer.get("report"),
        "input_tokens": answer.get("input_tokens"),
        "output_tokens": answer.get("output_tokens"),
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
        "_raw": raw_answer,
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


def mlflow_enabled() -> bool:
    """One gate for every MLflow sink this script writes (s43 M3 + s45 M3b).

    Defaults ON, matching s43 M3's original behaviour (and AGENTS.md):
    ``EVAL_MLFLOW=0`` opts out. Every sink is soft-fail, so a down tracking
    server degrades to a skipped mirror, never a failed ``make eval``. s45
    M3b added a second, heavier sink — one MLflow run per graded case, with
    the run's actual output as artifacts — sharing this flag and default.
    """
    return os.environ.get("EVAL_MLFLOW", "1") != "0"


_FINGERPRINT_CACHE: dict[str, Any] | None = None


def _fingerprint_components() -> dict[str, Any]:
    """The live agent build fingerprint, fetched once per eval invocation.

    Unlike the DB row (``app.agent_versions``, a fixed set of text columns —
    see ``version.build_fingerprint``'s report), ``/agent/version`` returns
    every component the running build computed, including the Agent SDK
    runtime's extra ones (workspace/ordinals/quota hashes) that have nowhere
    to live in the DB without a migration. Cached so N cases cost one HTTP
    call, not N, and so every case in one run is stamped with the same
    reading even if the agent wobbles mid-run.
    """
    global _FINGERPRINT_CACHE
    if _FINGERPRINT_CACHE is None:
        try:
            _FINGERPRINT_CACHE = _http(f"{AGENT}/agent/version", timeout=10)
        except Exception as exc:  # noqa: BLE001 — observability must not fail the eval
            print(f"    ! /agent/version unavailable for MLflow params: {exc}")
            _FINGERPRINT_CACHE = {}
    return _FINGERPRINT_CACHE


def _run_extras(run_id: str | None) -> dict[str, Any]:
    """cost_usd + the full trace for a query_run.

    Neither is on the ``/ask`` response for a non-admin replay user (cost_usd
    isn't returned to any caller; the trace is admin-gated — see
    ``backend-api/app/routers/ask.py``), so this reads them from the audit
    trail the same way ``_turns_for`` above already does. Only called when
    MLflow logging is on.
    """
    if not run_id:
        return {}
    out = _scalar(
        "SELECT row_to_json(t) FROM (SELECT cost_usd, trace, agent_version_id "
        f"FROM app.query_runs WHERE id = {_lit(run_id)}::uuid) t"
    )
    return json.loads(out) if out else {}


def log_case_mlflow(
    result: dict[str, Any],
    *,
    eval_run_id: str,
    experiment: str | None,
) -> None:
    """s44 M3b: one MLflow run per graded case, with its actual output logged
    as artifacts — the corpus a future optimisation loop (prompt/skill/
    workspace tuning, or a judge trained on real answers) reads from.

    Additive to ``log_mlflow``'s per-invocation summary above: that run is
    what the Evaluations tab's base-vs-experiment compare reads; this one is
    for looking at what the agent actually SAID on one specific case, with
    every fingerprint component available as a param even where the DB has no
    column for it. Both share the ``mlflow_enabled()`` gate. Soft-fail, same
    reasoning as ``log_mlflow``: a down tracking server must never fail
    `make eval`, so one case's logging failure is swallowed and reported, not
    raised.
    """
    if not mlflow_enabled():
        return
    if result.get("error"):
        return  # nothing ran; not worth a run row
    try:
        import mlflow_client as mc  # noqa: PLC0415 — optional sink, same dir

        fp = _fingerprint_components()
        extras = _run_extras(result.get("query_run_id"))
        exp_id = mc.ensure_experiment(mc.EVALS_EXPERIMENT)
        run_id = mc.start_run(
            exp_id,
            f"case · {result.get('case_key', '?')}",
            tags={
                "kind": "eval_case",
                "eval_run_id": eval_run_id,
                "case_key": result.get("case_key"),
                "experiment": experiment,
                "agent_version_id": extras.get("agent_version_id"),
            },
        )
        g1 = result.get("g1") or {}
        g2 = result.get("g2") or {}
        g3 = result.get("g3") or {}
        g3_format = g3.get("format") or {}
        g3_insight = g3.get("insight") or {}
        raw = result.get("_raw") or {}
        mc.log_batch(
            run_id,
            params={
                "case_key": result.get("case_key"),
                "dataset": result.get("dataset"),
                "tier": result.get("tier"),
                "model": fp.get("model_id"),
                "runtime": fp.get("runtime", "pydantic_ai"),
                "provider": fp.get("provider"),
                "agent_version_fingerprint": fp.get("fingerprint"),
                # Every fingerprint component the build computed, namespaced so
                # it never collides with the explicit params above — this is
                # what carries the Agent SDK runtime's individual components
                # (claude_md_hash, ordinals_hash, quota_settings, ...) that
                # app.agent_versions itself has no column for.
                **{f"fp_{k}": v for k, v in fp.items()},
            },
            metrics={
                "g1_score": g1.get("score"),
                "g2_score": g2.get("score"),
                "g3_format_passed": 1.0 if g3_format.get("passed") else 0.0,
                "g3_insight_total": g3_insight.get("total"),
                "passed": 1.0 if result.get("passed") else 0.0,
                "turns": (result.get("g4") or {}).get("turns"),
                "input_tokens": raw.get("input_tokens"),
                "output_tokens": raw.get("output_tokens"),
                "cost_usd": extras.get("cost_usd"),
                "latency_ms": result.get("latency_ms"),
            },
        )
        mc.log_text_artifact(run_id, "answer.txt", str(raw.get("answer") or ""))
        if raw.get("report") is not None:
            mc.log_json_artifact(run_id, "report.json", raw["report"])
        if extras.get("trace") is not None:
            mc.log_json_artifact(run_id, "trace.json", extras["trace"])
        mc.end_run(run_id)
    except Exception as exc:  # noqa: BLE001 — observability must not fail the eval
        print(f"    mlflow(case) · skipped ({exc})")


def log_mlflow(
    results: list[dict[str, Any]],
    totals: dict[str, Any],
    *,
    args: argparse.Namespace,
    pack_v: str,
    eval_run_id: str,
    agent_version_id: str | None,
) -> None:
    """s43 M3: mirror this eval into MLflow as one comparable run.

    Additive and soft-fail by design — the app.eval_runs write above is the
    source of truth, so a down tracking server must never fail `make eval`.
    Params carry the build fingerprint + the experiment framing; metrics carry
    the totals plus per-tier pass rates; tags carry the DB ids so the two
    stores reconcile. Skipped when ``EVAL_MLFLOW=0`` (see
    ``mlflow_enabled()``).
    """
    if not mlflow_enabled():
        return
    try:
        import mlflow_client as mc  # noqa: PLC0415 — optional sink, same dir

        exp_id = mc.ensure_experiment(mc.EVALS_EXPERIMENT)
        fingerprint: dict[str, Any] = {}
        if agent_version_id:
            out = _scalar(
                "SELECT row_to_json(t) FROM (SELECT fingerprint, provider, model_id, "
                "prompt_hash, skills_hash, knowledge_version, image_tag, git_sha "
                f"FROM app.agent_versions WHERE id = {_lit(agent_version_id)}::uuid) t"
            )
            fingerprint = json.loads(out) if out else {}
        run_name = f"eval · {args.experiment}" if args.experiment else "eval · baseline"
        run_id = mc.start_run(
            exp_id,
            run_name,
            tags={
                "kind": "eval",
                "eval_run_id": eval_run_id,
                "agent_version_id": agent_version_id,
                "base_run_id": getattr(args, "base", None),
            },
        )
        tiers = sorted({r.get("tier") for r in results if r.get("tier")})
        tier_metrics = {}
        for tier in tiers:
            of_tier = [r for r in results if r.get("tier") == tier]
            tier_metrics[f"pass_rate_{tier}"] = (
                sum(1 for r in of_tier if r.get("passed")) / len(of_tier) if of_tier else 0.0
            )
        mc.log_batch(
            run_id,
            params={
                "pack_version": pack_v,
                "dataset": args.dataset or "all",
                "tier": args.tier,
                "case": args.case_key,
                "hypothesis": args.hypothesis,
                "judge": "off" if args.no_judge else "on",
                **fingerprint,
            },
            metrics={
                "pass_rate": totals.get("pass_rate"),
                "passed": totals.get("passed"),
                "cases": totals.get("cases"),
                "errors": totals.get("errors"),
                "g1_mean": totals.get("g1_mean"),
                "g3_insight_mean": totals.get("g3_insight_mean"),
                "g4_turns_mean": totals.get("g4_turns_mean"),
                **tier_metrics,
            },
        )
        mc.end_run(run_id)
        print(f"mlflow · logged run {run_id} to {mc.EVALS_EXPERIMENT!r}")
    except Exception as exc:  # noqa: BLE001 — observability must not fail the eval
        print(f"mlflow · skipped ({exc})")


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
    version_id = (
        _scalar(f"SELECT agent_version_id FROM app.eval_runs WHERE id = {_lit(run_id)}::uuid")
        or None
    )
    log_mlflow(
        results, totals, args=args, pack_v=pack_v, eval_run_id=run_id, agent_version_id=version_id
    )
    # s44 M3b: one MLflow run per graded case (the eval_run_id above is only
    # known once persist() has written it, so this is a pass over the already-
    # graded results rather than logged inline during the scoring loop).
    if mlflow_enabled():
        for r in results:
            log_case_mlflow(r, eval_run_id=run_id, experiment=args.experiment)

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
