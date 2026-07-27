#!/usr/bin/env python3
"""Judge a slice of live traffic for quality drift (s32 W4).

The eval loop grades the agent against **goldens** — curated questions with known
answers. That is the right way to measure whether a change helped, and it is blind
to one thing: whether the answers real users are getting have quietly got worse on
questions nobody wrote a golden for. This samples recent live asks and scores them
with the same rubric the eval judge uses, so the deck carries an *advisory*
quality trend beside its uptime numbers.

Advisory is the operative word, and the limits are real:

* **It grades without ground truth.** The judge scores insight quality — grounded,
  direct, explains why, so-what, clear — not correctness. A confidently wrong
  answer can score well. Only a golden can catch a wrong number.
* **It is cross-family or nothing.** ``eval_judge`` refuses to grade a model of its
  own family (self-preference bias), so with DeepSeek answering this needs an
  Anthropic key. Without one it records nothing rather than fabricating a score.
* **It is a trend, not a gate.** Nothing blocks on it. A drop is a prompt to run
  ``make eval``, which does have ground truth.

Reads ``query_runs`` + ``messages`` directly (via the local compose DB, like
``scripts/eval_run.py``) and writes ``app.judge_samples`` through the data-agent's
grading endpoint. Sampling is by recency and capped, so the cost is bounded and
predictable: N judge calls, once per run.

Usage::

    python scripts/ops_judge_sample.py                 # 10 most recent unjudged asks
    python scripts/ops_judge_sample.py --limit 25
    python scripts/ops_judge_sample.py --hours 24 --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

AGENT = "http://localhost:8100"

# Answers shorter than this are refusals, errors, or "no answer" reports — judging
# them measures nothing and spends a model call each time.
MIN_ANSWER_CHARS = 120


def _psql(query: str) -> str:
    """Run a query as postgres inside the compose db container."""
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
            "-A",
            "-t",
            "-F",
            "\x1f",
            "-c",
            query,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout


def _candidates(*, limit: int, hours: int) -> list[dict[str, Any]]:
    """Recent successful chat answers that have not been judged yet.

    Excludes degraded/errored runs (a stub answer is not the agent's work) and
    anything already in ``judge_samples`` (idempotent, so a cron can run this
    hourly without re-paying for the same rows).
    """
    query = f"""
        SELECT qr.id, qr.question, m.content
        FROM app.query_runs qr
        JOIN app.messages m ON m.id = qr.message_id
        WHERE qr.source = 'agent'
          AND qr.status = 'success'
          AND NOT qr.degraded
          AND qr.channel = 'web'
          AND qr.created_at >= now() - interval '{hours} hours'
          AND length(m.content) >= {MIN_ANSWER_CHARS}
          AND NOT EXISTS (
              SELECT 1 FROM app.judge_samples js WHERE js.query_run_id = qr.id
          )
        ORDER BY qr.created_at DESC
        LIMIT {limit}
    """
    rows: list[dict[str, Any]] = []
    for line in _psql(query).splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        rows.append({"run_id": parts[0].strip(), "question": parts[1], "answer": parts[2]})
    return rows


def _judge(question: str, answer: str) -> dict[str, Any]:
    """Score one answer with the frozen insight rubric (the eval judge's own)."""
    # No golden, so no evidence block and no G1/G2 — insight only. That boundary
    # is why this is its own endpoint rather than a flag on /agent/eval/grade.
    payload = {"question": question, "answer": answer}
    request = urllib.request.Request(
        f"{AGENT}/agent/eval/judge",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as resp:
        return json.loads(resp.read())


def _record(run_id: str, verdict: dict[str, Any]) -> None:
    """Insert one judge_samples row.

    Written with psql rather than through the API because this is an offline
    analysis script, exactly like ``scripts/eval_run.py`` — keeping the write out
    of the API is what makes "a score can never be produced by clicking something
    in the UI" true of this table too.
    """
    payload = json.dumps(verdict).replace("'", "''")
    score = verdict.get("total")
    score_sql = "NULL" if score is None else str(float(score))
    _psql(
        "INSERT INTO app.judge_samples "
        "(query_run_id, judge_model, rubric_hash, insight_score, verdict) VALUES ("
        f"'{run_id}', "
        f"'{str(verdict.get('judge_model') or '').replace(chr(39), chr(39) * 2)}', "
        f"'{str(verdict.get('judge_prompt_hash') or '').replace(chr(39), chr(39) * 2)}', "
        f"{score_sql}, '{payload}'::jsonb)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="max asks to judge (cost bound)")
    parser.add_argument("--hours", type=int, default=24, help="how far back to sample")
    parser.add_argument("--dry-run", action="store_true", help="list candidates, judge nothing")
    args = parser.parse_args(argv)

    candidates = _candidates(limit=max(1, args.limit), hours=max(1, args.hours))
    if not candidates:
        print("no unjudged live answers in the window — nothing to do")
        return 0

    print(f"==> {len(candidates)} candidate(s)")
    if args.dry_run:
        for row in candidates:
            print(f"    {row['run_id']}  {row['question'][:80]}")
        return 0

    judged = skipped = 0
    for row in candidates:
        try:
            verdict = _judge(row["question"], row["answer"])
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            print(f"!! judge call failed for {row['run_id']}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        if verdict.get("skipped"):
            # The honest case: no cross-family judge configured. Recording a
            # skipped verdict as a score would invent data, so it is reported and
            # dropped — the same discipline the eval judge follows.
            print(f"   skipped {row['run_id']}: {verdict.get('reason')}")
            skipped += 1
            continue
        _record(row["run_id"], verdict)
        judged += 1
        print(f"   {row['run_id']}  insight {verdict.get('total')}/{verdict.get('max')}")

    print(f"==> judged {judged}, skipped {skipped}")
    if judged:
        # Fold the new samples into the deck immediately rather than waiting for
        # the next read to notice the rollup is stale.
        subprocess.run([sys.executable, "scripts/ops_ingest.py", "rollup"], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
