#!/usr/bin/env python3
"""Reflect on one eval run's failures and propose ONE prompt/knowledge edit (s49 M3).

    make reflect RUN=<eval_run_id>
    uv run python scripts/reflect.py --run <id> --dry-run

Where ``skill_miner.py`` improves what the agent *can do*, this improves what it is
*told*. For every case in an eval run that failed — or that the judge labelled ``low``
— it assembles the evidence a human reviewer would want (the question, the golden
answer, the judge's diagnosis and reason, the checkpoint scores, the decision log and
the extract SQL) and asks Opus for exactly one edit to either the system prompt
(``agent/prompts/workspace_claude.md``) or a single ``knowledge/**/*.md`` page — never
both, never code (decision D3 keeps knowledge a markdown filesystem, and code changes
belong to the skill miner).

The edit comes back as verbatim search/replace pairs, which this script applies inside
``.worktrees/optimise-<slug>`` and opens as a **draft** PR (D4). A model that cannot
quote the existing text exactly cannot change the file — which is the point: the edit is
grounded in what is actually on disk, not in a recollection of it.
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
    DbUnavailable,
    GapSignal,
    analysis_steps,
    ask_opus,
    commit_and_open_pr,
    db_rows,
    ensure_sdk,
    first_json_block,
    make_worktree,
    print_summary,
    render_pr_body,
    sandbox_code_from_trace,
    slugify,
)

ALLOWED_PREFIXES = (
    "services/data-agent/agent/prompts/workspace_claude.md",
    "services/data-agent/knowledge/",
)


class EditError(RuntimeError):
    """The model's edit does not apply — a hard stop, never a silent skip."""


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def failed_cases(eval_run_id: str) -> list[dict[str, Any]]:
    """Cases of one eval run that failed or that the judge called ``low``.

    ``judge`` is W-C's column; while it is still NULL this degrades to "the
    cases that failed", which is exactly the right fallback.
    """
    return db_rows(
        f"""
        select er.query_run_id::text as run_id, er.passed, er.judge, er.checkpoints,
               er.g1, er.g2, er.g3, er.g4, er.notes,
               ec.case_key, ec.dataset, ec.tier, ec.question, ec.expectation,
               ec.golden_answer, ec.label, ec.golden_sql,
               ec.checkpoints as case_checkpoints,
               r.sql_text, r.trace, r.artifact_manifest
        from app.eval_results er
        left join app.eval_cases ec on ec.id = er.case_id
        left join app.query_runs r on r.id = er.query_run_id
        where er.eval_run_id = '{eval_run_id}'
          and (er.passed is not true or er.judge->>'label' = 'low')
        """
    )


def _fmt_case(case: dict[str, Any]) -> str:
    judge = case.get("judge") or {}
    cp = case.get("checkpoints") or {}
    lines = [
        f"### case `{case.get('case_key') or case.get('run_id')}`"
        f" · {case.get('dataset') or '?'} · {case.get('tier') or '?'}",
        "",
        f"- question: {case.get('question') or ''}",
        f"- expectation: {case.get('expectation') or '(none)'}",
        f"- golden answer: {case.get('golden_answer') or '(not authored yet)'}",
        f"- passed: {case.get('passed')}",
        f"- judge: label={judge.get('label') or '—'} diagnosis={judge.get('diagnosis') or '—'}",
        f"- judge reason: {judge.get('reason') or '(none)'}",
        f"- checkpoints: {json.dumps(cp, sort_keys=True)[:600]}",
        f"- graders: g1={json.dumps(case.get('g1'))[:200]} g3={json.dumps(case.get('g3'))[:200]}",
        "",
    ]
    if case.get("golden_sql"):
        lines += ["Golden SQL:", "", "```sql", str(case["golden_sql"])[:1500], "```", ""]
    if case.get("sql_text"):
        sql = str(case["sql_text"])[:1500]
        lines += ["What the agent extracted:", "", "```sql", sql, "```", ""]
    code = sandbox_code_from_trace(case.get("trace"))
    if code:
        lines += ["What it computed:", "", "```python", code[:1500], "```", ""]
    steps = analysis_steps(case.get("trace"))
    if steps:
        used = ", ".join(str(s) for s in (steps[0].get("skills_used") or [])) or "(none)"
        gaps = [g.get("need") for g in (steps[0].get("skill_gaps") or []) if isinstance(g, dict)]
        gap_text = ", ".join(map(str, gaps)) or "(none)"
        lines += [f"- skills used: {used}", f"- skill gaps: {gap_text}", ""]
    manifest = case.get("artifact_manifest")
    if manifest:
        blob = json.dumps(manifest)[:1200]
        lines += ["Deck manifest (abridged):", "", "```json", blob, "```", ""]
    return "\n".join(lines)


def build_prompt(cases: list[dict[str, Any]], eval_run_id: str) -> str:
    body = [
        f"## Eval run `{eval_run_id}` — {len(cases)} case(s) failed or scored `low`",
        "",
    ]
    for case in cases:
        body.append(_fmt_case(case))
    body += [
        "## Your task",
        "",
        "Read the prompt/knowledge file you intend to change, then return the single "
        "json block your instructions specify. One file, minimal edit, falsifiable "
        "hypothesis — or an empty `edits` list with an honest explanation.",
    ]
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Applying the edit
# ---------------------------------------------------------------------------


def check_target(rel_path: str) -> str:
    """Reject anything outside the prompt / knowledge surface (rule 1)."""
    rel = rel_path.strip().lstrip("./")
    if not rel.startswith(ALLOWED_PREFIXES):
        raise EditError(
            f"{rel!r} is not editable by reflect — only "
            "agent/prompts/workspace_claude.md or a knowledge/**/*.md page"
        )
    if not rel.endswith(".md"):
        raise EditError(f"{rel!r} is not markdown — reflect never edits code")
    return rel


def apply_edits(worktree: Path, rel_path: str, edits: list[dict[str, str]]) -> None:
    """Apply verbatim, unique search/replace pairs. Any miss aborts the whole edit."""
    worktree_root = worktree.resolve()
    path = (worktree / rel_path).resolve()
    try:
        resolved_rel = path.relative_to(worktree_root).as_posix()
    except ValueError as exc:
        raise EditError(f"{rel_path!r} escapes the worktree") from exc
    if not resolved_rel.startswith(ALLOWED_PREFIXES):
        raise EditError(
            f"{rel_path!r} resolves to {resolved_rel!r}, outside the allowed "
            "prompt/knowledge surface"
        )
    if not path.is_file():
        raise EditError(f"{rel_path} does not exist")
    body = path.read_text(encoding="utf-8")
    for i, edit in enumerate(edits):
        old, new = edit.get("old") or "", edit.get("new") or ""
        if not old:
            raise EditError(f"edit {i} has no `old` text")
        found = body.count(old)
        if found != 1:
            raise EditError(f"edit {i}: `old` text appears {found} times (must be exactly 1)")
        body = body.replace(old, new, 1)
    path.write_text(body, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="eval_run id to reflect on")
    parser.add_argument("--base", default=DEFAULT_BASE_BRANCH, help="PR base branch")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the proposal; no branch, no PR"
    )
    args = parser.parse_args()
    ensure_sdk()

    try:
        cases = failed_cases(args.run)
    except DbUnavailable as exc:
        print(f"! cannot read the database: {exc}", file=sys.stderr)
        return 1

    if not cases:
        print_summary({"status": "no-failures", "pr_url": None, "branch": None, "files": []})
        print(f"eval run {args.run} has no failed or low-labelled cases — nothing to reflect on.")
        return 0

    keys = ", ".join(str(c.get("case_key")) for c in cases)
    print(f"{len(cases)} case(s) to reflect on: {keys}")
    system_prompt = (PROMPTS_DIR / "reflect.md").read_text(encoding="utf-8")
    reply = ask_opus(system_prompt=system_prompt, prompt=build_prompt(cases, args.run))

    meta = first_json_block(reply)
    if not meta:
        print(reply[-4000:], file=sys.stderr)
        print("! the model returned no json block", file=sys.stderr)
        return 1

    hypothesis = str(meta.get("hypothesis") or "")
    edits = [e for e in (meta.get("edits") or []) if isinstance(e, dict)]
    case_keys = [str(c.get("case_key")) for c in cases if c.get("case_key")]
    summary: dict[str, Any] = {
        "status": "ok",
        "hypothesis": hypothesis,
        "file": meta.get("file"),
        "cases": case_keys,
        "pr_url": None,
        "branch": None,
        "files": [],
    }

    print(f"\nhypothesis: {hypothesis}\n")
    if not edits:
        summary["status"] = "no-edit"
        print_summary(summary)
        print("the model proposed no edit (see the hypothesis above).")
        return 0

    rel = check_target(str(meta.get("file") or ""))
    print(f"proposed edit to {rel}:")
    for edit in edits:
        print(f"  - {str(edit.get('old'))[:120]!r}\n  + {str(edit.get('new'))[:120]!r}")

    if args.dry_run:
        summary["status"] = "dry-run"
        summary["files"] = [rel]
        print_summary(summary)
        return 0

    slug = slugify(str(meta.get("slug") or meta.get("summary") or "reflect"))
    branch = f"optimise/reflect-{slug}"
    worktree = make_worktree(slug, branch=branch)
    try:
        apply_edits(worktree, rel, edits)
        signals = [
            GapSignal(
                run_id=str(c.get("run_id") or ""),
                question=str(c.get("question") or ""),
                need=str((c.get("judge") or {}).get("diagnosis") or "failed"),
                why=str((c.get("judge") or {}).get("reason") or c.get("notes") or ""),
                source="judge",
                case_key=c.get("case_key"),
                dataset=c.get("dataset"),
            )
            for c in cases
        ]
        body = render_pr_body(
            kind="reflect",
            hypothesis=hypothesis,
            signals=signals,
            files=[rel],
            case_keys=case_keys,
            eval_run_id=args.run,
        )
        title = f"docs(agent): {meta.get('summary') or 'reflection edit'}"
        pr_url = commit_and_open_pr(
            worktree=worktree, branch=branch, title=title, body=body, files=[rel], base=args.base
        )
    except Exception:
        shutil.rmtree(worktree, ignore_errors=True)
        raise
    summary.update({"pr_url": pr_url, "branch": branch, "files": [rel]})
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
