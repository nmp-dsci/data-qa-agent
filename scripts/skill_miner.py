#!/usr/bin/env python3
"""Mine the run telemetry for a missing skill, prove it, open a draft PR (s49 M3).

    make skill-mine                 # sweep the last N runs
    make skill-mine RUN=<eval_run>  # only the cases of one eval run
    uv run python scripts/skill_miner.py --dry-run   # print, never write

The loop already tells us where the skill library is thin, in three voices:

* ``skills.skill_gap(need, why)`` — the runtime model saying "no skill covered this".
* ``skills.note_inline_math()`` — it did the maths by hand anyway.
* the judge's ``diagnosis = 'analysis'`` and a failed ``checkpoints.analysis`` —
  the graders saying the maths was wrong, whoever wrote it.

This script clusters those needs, asks Opus for ONE new ``@skill`` for the biggest
cluster, and then — the part that makes this more than a suggestion box — **executes
the candidate in the real sandbox over the affected golden's real rows** and checks it
produced the columns the golden's ``checkpoints.analysis.derived_cols`` asks for. Only a
candidate that passes gets a branch and a **draft** PR (decision D4); a candidate that
fails prints why and exits 1, because a skill nobody proved is worse than no skill: the
runtime model trusts whatever the library contains.

Read-only against Postgres (``admin_ro``). Never writes in the caller's working tree —
all edits happen in ``.worktrees/optimise-<slug>``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from optimiser_common import (  # noqa: E402
    DEFAULT_BASE_BRANCH,
    PROMPTS_DIR,
    Cluster,
    DbUnavailable,
    GapSignal,
    SandboxOutcome,
    analysis_steps,
    ask_opus,
    cluster_needs,
    commit_and_open_pr,
    db_rows,
    ensure_sdk,
    first_json_block,
    golden_truth,
    make_worktree,
    print_summary,
    python_blocks,
    render_pr_body,
    run_candidate_in_sandbox,
    sandbox_code_from_trace,
    slugify,
    strip_skill_decorator,
)

SKILLS_FILE = "services/data-agent/agent/skills/analysis.py"
DEFAULT_LIMIT = 200


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def _run_context(run_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Question / SQL / user / trace for a set of runs, one round trip."""
    if not run_ids:
        return {}
    ids = ", ".join(f"'{r}'" for r in run_ids)
    rows = db_rows(
        f"""
        select r.id::text as run_id, r.question, r.sql_text, r.trace,
               coalesce(u.username, 'user1') as username
        from app.query_runs r
        left join app.users u on u.id = r.user_id
        where r.id in ({ids})
        """
    )
    return {row["run_id"]: row for row in rows}


def sweep_runs(limit: int) -> list[GapSignal]:
    """Gaps + inline-maths flags from the most recent runs."""
    rows = db_rows(
        f"""
        select r.id::text as run_id, r.question,
               s->'skill_gaps' as skill_gaps,
               coalesce((s->>'used_inline_math')::bool, false) as inline_math
        from app.query_runs r
        cross join lateral jsonb_array_elements(r.trace) s
        where jsonb_typeof(r.trace) = 'array'
          and s->>'kind' = 'analysis'
          and (jsonb_array_length(coalesce(s->'skill_gaps', '[]'::jsonb)) > 0
               or coalesce((s->>'used_inline_math')::bool, false))
        order by r.created_at desc
        limit {int(limit)}
        """
    )
    signals: list[GapSignal] = []
    for row in rows:
        for gap in row.get("skill_gaps") or []:
            if isinstance(gap, dict) and gap.get("need"):
                signals.append(
                    GapSignal(
                        run_id=row["run_id"],
                        question=row.get("question") or "",
                        need=str(gap["need"]),
                        why=str(gap.get("why") or ""),
                    )
                )
        if row.get("inline_math"):
            signals.append(
                GapSignal(
                    run_id=row["run_id"],
                    question=row.get("question") or "",
                    need=f"inline maths for: {row.get('question') or ''}"[:200],
                    why="the model did the maths by hand instead of calling a skill",
                    source="inline_math",
                )
            )
    return signals


def eval_run_signals(eval_run_id: str) -> tuple[list[GapSignal], dict[str, dict[str, Any]]]:
    """Gaps + judge/checkpoint failures for the cases of one eval run.

    ``judge`` and ``checkpoints`` are W-C's columns and are frequently NULL while
    that workstream lands, so every read here tolerates their absence — an eval
    run with no judge verdict still yields its ``skill_gap`` signals.
    """
    rows = db_rows(
        f"""
        select er.query_run_id::text as run_id, er.passed, er.judge, er.checkpoints,
               ec.case_key, ec.dataset, ec.golden_sql, ec.as_user,
               ec.checkpoints as case_checkpoints
        from app.eval_results er
        left join app.eval_cases ec on ec.id = er.case_id
        where er.eval_run_id = '{eval_run_id}'
          and er.query_run_id is not null
        """
    )
    if not rows:
        return [], {}
    cases = {row["run_id"]: row for row in rows}
    ctx = _run_context(list(cases))

    signals: list[GapSignal] = []
    for run_id, case in cases.items():
        signals.extend(_case_signals(run_id, case, ctx.get(run_id, {})))
    return signals, cases


def _case_signals(run_id: str, case: dict[str, Any], run: dict[str, Any]) -> list[GapSignal]:
    """Every analysis-stage complaint one graded case made, in one list."""
    question = run.get("question") or ""
    common: dict[str, Any] = {
        "run_id": run_id,
        "question": question,
        "case_key": case.get("case_key"),
        "dataset": case.get("dataset"),
    }
    out: list[GapSignal] = []
    for step in analysis_steps(run.get("trace")):
        for gap in step.get("skill_gaps") or []:
            if isinstance(gap, dict) and gap.get("need"):
                out.append(
                    GapSignal(need=str(gap["need"]), why=str(gap.get("why") or ""), **common)
                )
        if step.get("used_inline_math"):
            out.append(
                GapSignal(
                    need=f"inline maths for: {question}"[:200],
                    why="the model did the maths by hand instead of calling a skill",
                    source="inline_math",
                    **common,
                )
            )

    judge = case.get("judge") or {}
    if isinstance(judge, dict) and judge.get("diagnosis") == "analysis":
        out.append(
            GapSignal(
                need=f"analysis-stage failure: {question}"[:200],
                why=str(judge.get("reason") or judge.get("label") or ""),
                source="judge",
                **common,
            )
        )

    cp = (case.get("checkpoints") or {}).get("analysis") or {}
    if isinstance(cp, dict) and cp.get("score") is not None and float(cp["score"]) < 1.0:
        missing = cp.get("missing_skills") or cp.get("missing") or []
        out.append(
            GapSignal(
                need=(f"analysis checkpoint missed: {', '.join(map(str, missing)) or question}")[
                    :200
                ],
                why=f"checkpoints.analysis score {cp['score']}",
                source="checkpoint",
                **common,
            )
        )
    return out


# ---------------------------------------------------------------------------
# The candidate
# ---------------------------------------------------------------------------


def build_prompt(cluster: Cluster, ctx: dict[str, dict[str, Any]]) -> str:
    """Everything Opus needs about this cluster that it cannot read off disk."""
    lines = [
        f"## The cluster ({cluster.size} observation(s))",
        "",
        f"Representative need: **{cluster.label}**",
        "",
    ]
    for signal in cluster.signals:
        lines += [
            f"- run `{signal.run_id[:8]}` ({signal.source})"
            + (f" · case `{signal.case_key}`" if signal.case_key else ""),
            f"  - question: {signal.question}",
            f"  - need: {signal.need}",
            f"  - why no skill fit: {signal.why or '(not said)'}",
        ]
    lines.append("")

    shown = 0
    for signal in cluster.signals:
        run = ctx.get(signal.run_id) or {}
        code = sandbox_code_from_trace(run.get("trace"))
        if not code or shown >= 3:
            continue
        shown += 1
        lines += [
            f"## What run `{signal.run_id[:8]}` actually wrote in the sandbox",
            "",
            "```python",
            code[:4000],
            "```",
            "",
        ]
        sql = run.get("sql_text")
        if sql:
            lines += [
                "Its extract SQL (so you know the column shape):",
                "",
                "```sql",
                sql[:2000],
                "```",
                "",
            ]

    lines += [
        "## Your task",
        "",
        "Write the ONE skill that would have served every observation above, following the "
        "output format in your instructions. Read `agent/skills/analysis.py` first.",
    ]
    return "\n".join(lines)


def truth_rows(
    cluster: Cluster, ctx: dict[str, dict[str, Any]], cases: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], str]:
    """Real rows to test the candidate against, plus a label saying where from.

    Preference order: the affected golden's ``golden_sql`` (the ground truth the
    case is graded on), then the run's own extract SQL (what the model actually
    saw). Both go through the governed ``/sql`` path under the right user, so RLS
    applies exactly as it did at run time.
    """
    for signal in cluster.signals:
        case = cases.get(signal.run_id) or {}
        sql = case.get("golden_sql")
        if sql:
            rows = golden_truth(sql, as_user=case.get("as_user") or "user1")
            if rows:
                return rows, f"golden_sql of {case.get('case_key')}"
    for signal in cluster.signals:
        run = ctx.get(signal.run_id) or {}
        sql = run.get("sql_text")
        if sql:
            rows = golden_truth(sql, as_user=run.get("username") or "user1")
            if rows:
                return rows, f"extract SQL of run {signal.run_id[:8]}"
    return [], "none"


def expected_cols(
    cluster: Cluster, cases: dict[str, dict[str, Any]], meta: dict[str, Any]
) -> list[str]:
    """``checkpoints.analysis.derived_cols`` for an affected case, else the model's."""
    for signal in cluster.signals:
        case = cases.get(signal.run_id) or {}
        cp = (case.get("case_checkpoints") or {}).get("analysis") or {}
        cols = cp.get("derived_cols") if isinstance(cp, dict) else None
        if cols:
            return [str(c) for c in cols]
    return [str(c) for c in meta.get("expect_cols") or []]


def test_candidate(
    *, skill_src: str, meta: dict[str, Any], rows: list[dict[str, Any]], expect_cols: list[str]
) -> SandboxOutcome:
    """Run ``<candidate> + <sandbox_test>`` in the real sandbox over ``rows``."""
    snippet = str(meta.get("sandbox_test") or "").strip()
    if not snippet:
        return SandboxOutcome(ok=False, error="the model gave no sandbox_test snippet")
    if not rows:
        return SandboxOutcome(ok=False, error="no truth rows available (is the local stack up?)")
    code = f"{strip_skill_decorator(skill_src)}\n\n{snippet}\n"
    return run_candidate_in_sandbox(code=code, rows=rows, expect_cols=expect_cols)


# ---------------------------------------------------------------------------
# Writing the change
# ---------------------------------------------------------------------------


def apply_candidate(worktree: Path, *, skill_src: str, test_src: str, name: str) -> list[str]:
    """Append the skill to ``analysis.py`` and drop its unit test beside the others."""
    skills_path = worktree / SKILLS_FILE
    body = skills_path.read_text(encoding="utf-8").rstrip("\n")
    skills_path.write_text(f"{body}\n\n\n{skill_src.strip()}\n", encoding="utf-8")

    test_rel = f"services/data-agent/tests/test_skill_{name}.py"
    (worktree / test_rel).write_text(test_src.strip() + "\n", encoding="utf-8")
    return [SKILLS_FILE, test_rel]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", help="eval_run id; without it, sweep recent chat runs")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="runs to sweep")
    parser.add_argument("--base", default=DEFAULT_BASE_BRANCH, help="PR base branch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the cluster, candidate and test result; no branch, no PR",
    )
    args = parser.parse_args()
    # Re-exec into an environment that has claude-agent-sdk BEFORE any work, so
    # the DB sweep is not paid for twice (os.execvpe restarts this process).
    ensure_sdk()

    try:
        if args.run:
            signals, cases = eval_run_signals(args.run)
        else:
            signals, cases = sweep_runs(args.limit), {}
    except DbUnavailable as exc:
        print(f"! cannot read the database: {exc}", file=sys.stderr)
        return 1

    if not signals:
        print_summary({"status": "no-signal", "pr_url": None, "branch": None, "files": []})
        print("no skill gaps, inline maths or analysis diagnoses found — nothing to mine.")
        return 0

    clusters = cluster_needs(signals)
    print(f"{len(signals)} signal(s) in {len(clusters)} cluster(s):")
    for c in clusters:
        print(f"  [{c.size}] {c.label}")
    cluster = clusters[0]
    ctx = _run_context(cluster.run_ids)

    print(f"\nasking Opus for one skill covering: {cluster.label}\n")
    system_prompt = (PROMPTS_DIR / "skill_miner.md").read_text(encoding="utf-8")
    reply = ask_opus(system_prompt=system_prompt, prompt=build_prompt(cluster, ctx))

    meta = first_json_block(reply)
    blocks = python_blocks(reply)
    if not meta.get("name") or len(blocks) < 2:
        print(reply[-4000:], file=sys.stderr)
        print("! the model did not return {json, skill, test} blocks", file=sys.stderr)
        return 1
    name = str(meta["name"])
    skill_src, test_src = blocks[0], blocks[1]
    slug = slugify(str(meta.get("slug") or name))

    print(f"candidate: {name}\n")
    print(skill_src)
    print()

    rows, source = truth_rows(cluster, ctx, cases)
    cols = expected_cols(cluster, cases, meta)
    print(f"testing against {len(rows)} row(s) from {source}; expecting columns {cols or '(none)'}")
    outcome = test_candidate(skill_src=skill_src, meta=meta, rows=rows, expect_cols=cols)
    print(json.dumps(outcome.as_dict(), indent=2))

    summary: dict[str, Any] = {
        "status": "ok" if outcome.ok else "candidate-failed",
        "skill": name,
        "cluster": cluster.label,
        "runs": cluster.run_ids,
        "hypothesis": str(meta.get("hypothesis") or ""),
        "test": outcome.as_dict(),
        "pr_url": None,
        "branch": None,
        "files": [],
    }

    if not outcome.ok:
        print_summary(summary)
        print("! candidate failed its offline test — not opening a PR", file=sys.stderr)
        return 1

    if args.dry_run:
        summary["status"] = "dry-run"
        print_summary(summary)
        return 0

    branch = f"optimise/skill-{slug}"
    worktree = make_worktree(slug, branch=branch)
    try:
        files = apply_candidate(worktree, skill_src=skill_src, test_src=test_src, name=name)
        body = render_pr_body(
            kind="skill",
            hypothesis=str(meta.get("hypothesis") or ""),
            signals=cluster.signals,
            files=files,
            test_result=outcome.as_dict(),
            case_keys=[s.case_key for s in cluster.signals if s.case_key],
            eval_run_id=args.run,
        )
        title = f"feat(skills): {meta.get('summary') or name}"
        pr_url = commit_and_open_pr(
            worktree=worktree, branch=branch, title=title, body=body, files=files, base=args.base
        )
    except Exception:
        shutil.rmtree(worktree, ignore_errors=True)
        raise
    summary.update({"pr_url": pr_url, "branch": branch, "files": files})
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
