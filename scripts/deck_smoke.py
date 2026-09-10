#!/usr/bin/env python3
"""End-to-end check: three questions in, three Slides decks out (s46).

This is the proof the migration works. It asks three questions through the real
`/ask` path — same auth, same RLS, same governed tools the browser uses — and
then asserts on the artifact the user would actually receive: a deck and a sheet
that exist, slides that carry headlines, and at least one native chart.

What it deliberately does NOT assert: which layout the agent picked. Layout
choice is a model decision now, so pinning it would grade a coin flip and fail
runs that are perfectly good.

Prerequisites (all of them, or this exits telling you which is missing):
  * The stack is up: `make up`
  * AGENT_RUNTIME=agent_sdk and CLAUDE_CODE_OAUTH_TOKEN set
  * GOOGLE_DECK_CLIENT_ID / _SECRET / _REFRESH_TOKEN set
    (`uv run python scripts/google_auth.py --write-env`), and DECK_EXPORT=1

    uv run python scripts/deck_smoke.py
    uv run python scripts/deck_smoke.py --question "your own question"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("API_URL", f"http://localhost:{os.environ.get('API_HOST_PORT', '8010')}")

# One per shape the catalogue is meant to cover: a single trend, a comparison
# across groups, and a ranking whose rows are the answer.
QUESTIONS = [
    "What's the rent trend for postcode 2077 over the last 3 years?",
    "Compare median sale prices between Hornsby and Normanhurst.",
    "Which suburbs have the highest rental yields?",
]


def _http(path: str, *, body: dict | None = None, token: str = "", timeout: int = 300) -> dict:
    req = urllib.request.Request(  # noqa: S310 — localhost API
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode()))


def _check_prereqs() -> list[str]:
    """Names of missing credentials, checking `.env` as well as the environment.

    The container reads these from `.env` via compose, so a shell that never
    exported them is still a perfectly working setup — checking only os.environ
    would fail the smoke test on a stack that is fine.
    """
    env_file: set[str] = set()
    path = Path(".env")
    if path.exists():
        env_file = {
            line.split("=", 1)[0].strip()
            for line in path.read_text().splitlines()
            if "=" in line and not line.lstrip().startswith("#") and line.split("=", 1)[1].strip()
        }
    return [
        name
        for name in (
            "GOOGLE_DECK_CLIENT_ID",
            "GOOGLE_DECK_CLIENT_SECRET",
            "GOOGLE_DECK_REFRESH_TOKEN",
        )
        if not os.environ.get(name) and name not in env_file
    ]


def _verify(artifact: dict | None) -> list[str]:
    """Assert on content, never on layout identity."""
    if not artifact:
        return ["no artifact — is DECK_EXPORT=1 and AGENT_RUNTIME=agent_sdk in the container?"]
    problems: list[str] = []
    for key in ("deck_url", "embed_url", "sheet_url"):
        if not artifact.get(key):
            problems.append(f"missing {key}")
    slides = artifact.get("slides") or []
    if not slides:
        problems.append("deck has no slides")
    for slide in slides:
        if not str(slide.get("headline") or "").strip():
            problems.append(f"slide {slide.get('index')} has no headline")
    if not any(s.get("has_chart") or s.get("has_table") for s in slides):
        problems.append("no slide carries a chart or a table")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", action="append", help="override (repeatable)")
    parser.add_argument("--user", default="user1", help="dev-login username")
    args = parser.parse_args()

    missing = _check_prereqs()
    if missing:
        print(f"Missing env: {', '.join(missing)}", file=sys.stderr)
        print("Run: uv run python scripts/google_auth.py --write-env", file=sys.stderr)
        return 2

    questions = args.question or QUESTIONS
    try:
        token = _http("/auth/dev-login", body={"username": args.user}, timeout=30)["access_token"]
    except urllib.error.URLError as exc:
        print(f"Cannot reach {API}: {exc}. Is the stack up (`make up`)?", file=sys.stderr)
        return 2

    failures = 0
    for i, question in enumerate(questions, start=1):
        print(f"\n[{i}/{len(questions)}] {question}")
        try:
            answer = _http("/ask", body={"question": question}, token=token)
        except Exception as exc:  # noqa: BLE001 — report and keep going
            print(f"  FAILED: {exc}")
            failures += 1
            continue

        artifact = answer.get("artifact")
        problems = _verify(artifact)
        if problems:
            failures += 1
            print("  FAILED:")
            for p in problems:
                print(f"    - {p}")
            continue

        slides = artifact["slides"]  # type: ignore[index]
        print(f"  answer:  {(answer.get('answer') or '')[:100]}")
        print(f"  slides:  {len(slides)}")
        for s in slides:
            kind = "chart" if s["has_chart"] else ("table" if s["has_table"] else "text")
            print(f"    {s['index'] + 1}. [{s['layout']}] {s['headline']}  ({kind})")
        print(f"  deck:    {artifact['deck_url']}")  # type: ignore[index]
        print(f"  sheet:   {artifact['sheet_url']}")  # type: ignore[index]

    print(f"\n{len(questions) - failures}/{len(questions)} questions produced a valid deck.")
    if failures:
        return 1
    print("Open a deck link above, then check the same answer renders in the app at :5230.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
