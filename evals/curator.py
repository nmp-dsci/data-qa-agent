"""Curator (K6) — turn feedback + eval failures into a knowledge-edit PROPOSAL.

The learning loop's write step, run with a human in the loop. It reads the
admin feedback + eval-case signals from a live stack, groups them by the
knowledge pages that produced the flagged answers, and writes a markdown
PROPOSAL describing which pages likely need edits and why. It deliberately does
NOT edit knowledge/*.md — a human (or a coding-agent session) reads the proposal
and opens the actual PR, so nothing that steers SQL generation changes without
review.

Usage:
    python evals/curator.py [--api http://localhost:8000] [--user admin]

Writes evals/curator_proposals/proposal-<timestamp>.md and prints its path.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _req(api: str, method: str, path: str, body: dict | None, token: str | None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(api + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--user", default="admin")
    args = ap.parse_args()

    try:
        login = _req(args.api, "POST", "/auth/dev-login", {"username": args.user}, None)
    except urllib.error.URLError as exc:
        print(f"could not reach stack at {args.api}: {exc}", file=sys.stderr)
        return 2
    token = login["access_token"]

    feedback = _req(args.api, "GET", "/admin/feedback", None, token)
    eval_cases = _req(args.api, "GET", "/admin/eval-cases", None, token)

    # Group negative / promoted signals by the knowledge pages that produced them.
    by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unattributed: list[dict[str, Any]] = []
    for f in feedback:
        if f["rating"] == 1 and f["status"] != "promoted_to_eval":
            continue  # only learn from negative or promoted signals
        pages = f.get("knowledge_pages") or []
        if pages:
            for page in pages:
                by_page[page].append(f)
        else:
            unattributed.append(f)

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = HERE / "curator_proposals"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"proposal-{ts}.md"

    lines: list[str] = [
        f"# Knowledge-edit proposal — {ts}",
        "",
        "> Draft for human review. Apply the edits you agree with as a normal PR to",
        "> `services/data-agent/knowledge/`, then re-run the evals. Nothing here is",
        "> auto-applied.",
        "",
        f"Signals: {len(feedback)} feedback item(s), {len(eval_cases)} eval case(s).",
        "",
    ]

    if by_page:
        lines.append("## Pages implicated by feedback\n")
        for page, items in sorted(by_page.items(), key=lambda kv: -len(kv[1])):
            neg = sum(1 for i in items if i["rating"] == -1)
            lines.append(f"### `{page}` — {len(items)} signal(s), {neg} negative")
            for i in items:
                comment = (i.get("comment") or "").strip() or "(no comment)"
                lines.append(
                    f"- [{'👎' if i['rating'] == -1 else '👍'}] on {i['target_kind']} "
                    f"`{i['target_ref']}` (knowledge @ {i['knowledge_version'][:7]}): {comment}"
                )
            lines.append("")
            lines.append(
                "  **Suggested action:** review this page for the theme in the comments "
                "above — often a new `## Learned pitfalls` bullet, a clarified rule, or a "
                "cross-link. Confirm against the schema before changing any SQL guidance.\n"
            )

    if unattributed:
        lines.append("## Feedback with no attributed knowledge page\n")
        for i in unattributed:
            comment = (i.get("comment") or "").strip() or "(no comment)"
            lines.append(f"- [{'👎' if i['rating'] == -1 else '👍'}] {i['target_kind']}: {comment}")
        lines.append(
            "\n  **Suggested action:** these may need a NEW knowledge page or a better "
            "`applies_to` on an existing one so the agent loads the right guidance.\n"
        )

    stale = [c for c in eval_cases if c["status"] == "stale"]
    if stale:
        lines.append("## Stale eval cases (referent changed)\n")
        for c in stale:
            lines.append(f"- {c['question']} — expectation: {c['expectation']}")
        lines.append(
            "\n  **Suggested action:** re-affirm (toggle active) or archive in the admin "
            "panel; the report element they judged has materially changed.\n"
        )

    if not by_page and not unattributed and not stale:
        lines.append("_No actionable negative signals right now — nothing to propose._")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
