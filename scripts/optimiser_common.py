#!/usr/bin/env python3
"""Shared machinery for the offline optimiser (s49 M3, W-D).

``skill_miner.py`` and ``reflect.py`` are two halves of the same idea: read the
telemetry the eval loop already writes, ask a smart model (Opus) for ONE
improvement, prove it offline where that is possible, and open a **draft PR**
(decision D4 — the optimiser never commits to ``main`` or to a challenger
branch). Everything they share lives here: DB reads, the Agent SDK bridge, the
candidate-test harness, the git/worktree/PR mechanics and the PR body renderer.

Design constraints that shape this module:

* **Read-only against Postgres.** Reads go through the ``admin_ro`` role
  (``ADMIN_RO_DATABASE_URL``, BYPASSRLS + SELECT only), executed with ``psql``
  inside the compose ``db`` container because the host has no libpq — the same
  trick ``scripts/eval_run.py`` uses.
* **No new root dependencies.** The Makefile invokes these as plain
  ``uv run python scripts/...`` at the repo root, whose environment carries
  neither ``claude-agent-sdk`` nor ``pandas``. So the SDK is reached by
  re-execing under ``uv run --with claude-agent-sdk`` (:func:`ensure_sdk`) and
  the sandbox is reached by shelling into the data-agent's own environment
  (:func:`run_candidate_in_sandbox`).
* **Never touch the caller's working tree.** Every write happens inside
  ``.worktrees/optimise-<slug>``, a fresh ``git worktree`` off HEAD.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_AGENT_DIR = REPO_ROOT / "services" / "data-agent"
PROMPTS_DIR = REPO_ROOT / "evals" / "optimiser"
WORKTREES_DIR = REPO_ROOT / ".worktrees"

OPUS_MODEL = "claude-opus-5"
DEFAULT_BASE_BRANCH = "eval-loop-review"
DEFAULT_ADMIN_RO_URL = "postgresql+asyncpg://admin_ro:admin_pw@db:5432/dataqa"


# ---------------------------------------------------------------------------
# .env / config
# ---------------------------------------------------------------------------


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """The repo ``.env`` as a dict, with the shell environment winning.

    Compose reads ``.env`` itself, but these scripts run on the host, so a
    machine that remapped a port (``API_HOST_PORT=8010`` here) or points at a
    different database needs the same values — the same reasoning as
    ``scripts/eval_run.py::_host_port``.
    """
    env_path = path or (REPO_ROOT / ".env")
    out: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip("'\"")
    for key in list(out):
        if os.environ.get(key):
            out[key] = os.environ[key]
    for key in ("ADMIN_RO_DATABASE_URL", "API_HOST_PORT", "CLAUDE_CODE_OAUTH_TOKEN"):
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def _libpq_url(url: str) -> str:
    """``postgresql+asyncpg://...`` -> ``postgresql://...`` for psql.

    The compose value points at the ``db`` host name, which only resolves from
    inside the compose network — which is exactly where we run psql.
    """
    return re.sub(r"^postgresql\+[a-z]+://", "postgresql://", url)


def admin_ro_url() -> str:
    env = load_dotenv()
    return _libpq_url(env.get("ADMIN_RO_DATABASE_URL") or DEFAULT_ADMIN_RO_URL)


def api_base() -> str:
    env = load_dotenv()
    return f"http://localhost:{env.get('API_HOST_PORT') or '8000'}"


# ---------------------------------------------------------------------------
# Read-only DB access
# ---------------------------------------------------------------------------


class DbUnavailable(RuntimeError):
    """The compose db container could not answer — the caller decides if fatal."""


def db_rows(sql: str) -> list[dict[str, Any]]:
    """Run a SELECT as ``admin_ro`` and return its rows as dicts.

    The query is wrapped in ``json_agg`` so one psql round trip returns typed
    JSON instead of pipe-delimited text that would have to be re-parsed (and
    that loses nulls vs empty strings, which matters for judge/checkpoint
    columns that are frequently NULL while W-C is still landing).
    """
    wrapped = f"select coalesce(json_agg(t), '[]'::json) from ({sql.rstrip().rstrip(';')}) t"
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", admin_ro_url(), "-tAc", wrapped],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise DbUnavailable(proc.stderr.strip() or "psql failed")
    out = proc.stdout.strip()
    if not out:
        return []
    parsed = json.loads(out)
    return list(parsed) if isinstance(parsed, list) else []


# ---------------------------------------------------------------------------
# Signals: skill gaps, inline maths, judge diagnoses
# ---------------------------------------------------------------------------


@dataclass
class GapSignal:
    """One "no skill covered this" observation, tied back to the run that made it."""

    run_id: str
    question: str
    need: str
    why: str = ""
    source: str = "skill_gap"  # skill_gap | inline_math | judge | checkpoint
    case_key: str | None = None
    dataset: str | None = None
    code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "question": self.question,
            "need": self.need,
            "why": self.why,
            "source": self.source,
            "case_key": self.case_key,
            "dataset": self.dataset,
        }


@dataclass
class Cluster:
    """A group of near-identical needs — the unit a candidate skill answers."""

    label: str
    signals: list[GapSignal] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.signals)

    @property
    def run_ids(self) -> list[str]:
        seen: list[str] = []
        for s in self.signals:
            if s.run_id and s.run_id not in seen:
                seen.append(s.run_id)
        return seen


_STOPWORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "over",
    "per",
    "the",
    "then",
    "to",
    "with",
}


def normalise_need(need: str) -> str:
    """Lower-case, de-punctuated, stop-worded — the clustering key material."""
    words = re.findall(r"[a-z0-9_]+", need.lower())
    return " ".join(w for w in words if w not in _STOPWORDS)


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return max(jaccard, difflib.SequenceMatcher(None, a, b).ratio())


def cluster_needs(signals: Iterable[GapSignal], *, threshold: float = 0.55) -> list[Cluster]:
    """Greedy single-link clustering of needs, biggest cluster first.

    Deliberately not k-means/embeddings: with the handful of gaps a young pack
    produces, a transparent string-similarity rule is easier to trust and to
    unit-test, and its failure mode (two clusters that should be one) merely
    costs a second mining run.
    """
    clusters: list[Cluster] = []
    keys: list[str] = []
    for signal in signals:
        key = normalise_need(signal.need)
        best_i, best_score = -1, 0.0
        for i, existing in enumerate(keys):
            score = _similar(key, existing)
            if score > best_score:
                best_i, best_score = i, score
        if best_i >= 0 and best_score >= threshold:
            clusters[best_i].signals.append(signal)
        else:
            clusters.append(Cluster(label=signal.need, signals=[signal]))
            keys.append(key)
    # Stable sort: size desc, then first-seen order (list.sort is stable).
    clusters.sort(key=lambda c: c.size, reverse=True)
    return clusters


def sandbox_code_from_trace(trace: Any) -> str:
    """The first ``run_analysis`` script in a run's trace, imports stripped.

    Mirrors ``services/backend-api/app/routers/goldens.py::_sandbox_code_from_steps``
    — same recovery rule, duplicated rather than imported because that module
    pulls the whole FastAPI backend in.
    """
    steps = trace if isinstance(trace, list) else (trace or {}).get("steps") or []
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") != "model":
            continue
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict) or call.get("name") != "run_analysis":
                continue
            args = call.get("args")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            code = args.get("code") if isinstance(args, dict) else None
            if code:
                lines = [
                    ln
                    for ln in str(code).splitlines()
                    if not re.match(r"\s*(import |from \S+ import )", ln)
                ]
                return "\n".join(lines).strip()
    return ""


def analysis_steps(trace: Any) -> list[dict[str, Any]]:
    steps = trace if isinstance(trace, list) else (trace or {}).get("steps") or []
    return [s for s in steps if isinstance(s, dict) and s.get("kind") == "analysis"]


# ---------------------------------------------------------------------------
# The Claude Agent SDK bridge
# ---------------------------------------------------------------------------


class SdkUnavailable(RuntimeError):
    pass


_REEXEC_FLAG = "OPTIMISER_SDK_REEXEC"


def ensure_sdk() -> Any:
    """Import ``claude_agent_sdk``, re-execing into an env that has it if needed.

    ``make skill-mine`` runs ``uv run python scripts/skill_miner.py`` in the repo
    root environment, which deliberately carries only sqlglot/pydantic — the
    SDK lives behind the data-agent's ``agentsdk`` extra. Rather than widen the
    root dependency set (and rather than make the Makefile line conditional),
    the script re-launches itself once under ``uv run --with claude-agent-sdk``.
    The flag env var makes the recursion impossible.
    """
    try:
        import claude_agent_sdk  # type: ignore[import-not-found]  # noqa: PLC0415

        return claude_agent_sdk
    except ImportError:
        pass
    if os.environ.get(_REEXEC_FLAG):
        raise SdkUnavailable(
            "claude-agent-sdk is still missing after re-exec; install it with "
            "`uv run --with claude-agent-sdk ...` or `uv sync --extra agentsdk` "
            "in services/data-agent"
        )
    env = dict(os.environ, **{_REEXEC_FLAG: "1"})
    argv = ["uv", "run", "--with", "claude-agent-sdk", "python", *sys.argv]
    os.execvpe("uv", argv, env)  # noqa: S606 — fixed argv, no shell


def cli_env() -> dict[str, str]:
    """Blank provider keys so the CLI uses the subscription/OAuth login.

    Same reasoning as ``agent/sdk_agent.py::cli_env``: ``ClaudeAgentOptions.env``
    is merged over the inherited environment and cannot delete a key, and a
    visible ``ANTHROPIC_API_KEY`` silently flips the CLI to per-token billing.
    """
    dotenv = load_dotenv()
    env = {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "", "DEEPSEEK_API_KEY": ""}
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or dotenv.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]


def ask_opus(
    *,
    system_prompt: str,
    prompt: str,
    cwd: Path | None = None,
    max_turns: int = 30,
    sdk: Any | None = None,
) -> str:
    """One Opus turn-loop with read-only tools; returns the concatenated text.

    The model is given Read/Grep/Glob over the repo so it can go and look at the
    real ``agent/skills/analysis.py`` (or the prompt/knowledge page) rather than
    being handed a lossy paraphrase of it. It has no Write/Edit/Bash: the diff
    comes back as fenced blocks that *this* script applies inside a worktree,
    which keeps the "never modify the caller's tree" guarantee mechanical rather
    than a matter of the model's good behaviour.
    """
    import asyncio  # noqa: PLC0415

    sdk = sdk or ensure_sdk()
    options = sdk.ClaudeAgentOptions(
        model=OPUS_MODEL,
        cwd=str(cwd or REPO_ROOT),
        system_prompt=system_prompt,
        max_turns=max_turns,
        tools=list(READ_ONLY_TOOLS),
        allowed_tools=list(READ_ONLY_TOOLS),
        env=cli_env(),
    )

    async def _run() -> str:
        chunks: list[str] = []
        async for msg in sdk.query(prompt=prompt, options=options):
            for block in getattr(msg, "content", None) or []:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks)

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Parsing the model's reply
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    """Every fenced block as ``(language, body)``, in order."""
    return [(m.group(1).lower(), m.group(2).strip("\n")) for m in _FENCE.finditer(text)]


def first_json_block(text: str) -> dict[str, Any]:
    """The first ```json block, parsed. ``{}`` when there is none/it is broken."""
    for lang, body in fenced_blocks(text):
        if lang == "json":
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def python_blocks(text: str) -> list[str]:
    return [body for lang, body in fenced_blocks(text) if lang in ("python", "py")]


def slugify(text: str, *, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "candidate"


# ---------------------------------------------------------------------------
# Candidate testing: the real sandbox, over real golden rows
# ---------------------------------------------------------------------------


@dataclass
class SandboxOutcome:
    ok: bool
    error: str | None = None
    stdout: str = ""
    frames: list[dict[str, Any]] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    missing_cols: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "frames": [f.get("name") for f in self.frames],
            "skills_used": self.skills_used,
            "missing_cols": self.missing_cols,
        }


def strip_skill_decorator(source: str) -> str:
    """Drop ``@skill`` / ``@skills.skill`` lines from a candidate.

    The decorator is pure telemetry (it appends to ``skills._USED``), and the
    sandbox namespace does not expose it to model-written code — so the offline
    test executes the undecorated function body, which is the part whose maths
    we are actually trying to prove.
    """
    return "\n".join(
        ln for ln in source.splitlines() if not re.match(r"\s*@(skills\.)?skill\b", ln)
    )


def run_candidate_in_sandbox(
    *,
    code: str,
    rows: Sequence[dict[str, Any]],
    expect_cols: Sequence[str] = (),
    timeout_s: int = 180,
) -> SandboxOutcome:
    """Execute ``code`` in the REAL sandbox over ``rows``, checked for ``expect_cols``.

    Shelled into ``services/data-agent`` because ``agent.sandbox`` needs pandas
    and the agent's settings, neither of which the repo-root environment has.
    ``SANDBOX_RUNTIME=subprocess`` keeps this Node-free on a developer host (the
    container default is pyodide; the maths under test is identical on both).
    """
    probe = REPO_ROOT / "scripts" / "optimiser_sandbox_probe.py"
    payload = json.dumps({"code": code, "rows": list(rows), "expect_cols": list(expect_cols)})
    proc = subprocess.run(
        ["uv", "run", "--extra", "llm", "python", str(probe)],
        cwd=DATA_AGENT_DIR,
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=dict(os.environ, SANDBOX_RUNTIME="subprocess"),
    )
    if proc.returncode != 0:
        return SandboxOutcome(ok=False, error=(proc.stderr or "sandbox probe failed")[-2000:])
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return SandboxOutcome(ok=False, error=f"unparseable probe output: {proc.stdout[-2000:]!r}")
    return SandboxOutcome(
        ok=bool(result.get("ok")),
        error=result.get("error"),
        stdout=result.get("stdout") or "",
        frames=result.get("frames") or [],
        skills_used=result.get("skills_used") or [],
        missing_cols=result.get("missing_cols") or [],
    )


# ---------------------------------------------------------------------------
# Golden truth rows (the same recipe scripts/eval_run.py uses)
# ---------------------------------------------------------------------------


def _http(url: str, *, body: dict[str, Any], token: str | None = None, timeout: int = 120) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — localhost only
        return json.loads(resp.read().decode())


def dev_login(username: str = "user1") -> str:
    return str(_http(f"{api_base()}/auth/dev-login", body={"username": username})["access_token"])


def golden_truth(sql: str, *, as_user: str = "user1") -> list[dict[str, Any]]:
    """Re-run a golden's ``golden_sql`` through the governed ``/sql`` path.

    Recomputed, never read from the pack: the candidate must be proved against
    the rows the mart returns *today*, which is what the agent would have seen.
    Returns ``[]`` (and says why) when the stack is not up — the caller decides
    whether that is fatal.
    """
    if not sql:
        return []
    try:
        token = dev_login(as_user)
        result = _http(f"{api_base()}/sql", body={"sql": sql}, token=token)
    except (urllib.error.URLError, OSError, KeyError) as exc:
        print(f"    ! golden_sql could not run ({exc}); no truth rows", file=sys.stderr)
        return []
    return rows_as_dicts(result)


def rows_as_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise a ``{columns, rows}`` result into row dicts (eval_run's rule)."""
    cols = [c["name"] if isinstance(c, dict) else str(c) for c in payload.get("columns") or []]
    out: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)):
            out.append({str(c): v for c, v in zip(cols, row, strict=False)})
    return out


# ---------------------------------------------------------------------------
# git worktree + draft PR (D4: draft only, never a direct commit)
# ---------------------------------------------------------------------------


def _git(args: Sequence[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd or REPO_ROOT, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def ensure_worktrees_ignored() -> None:
    """``.worktrees/`` must never be committed — add the ignore line if missing."""
    gitignore = REPO_ROOT / ".gitignore"
    body = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    if any(ln.strip() in (".worktrees/", ".worktrees") for ln in body.splitlines()):
        return
    with gitignore.open("a", encoding="utf-8") as fh:
        fh.write(("" if body.endswith("\n") or not body else "\n") + ".worktrees/\n")


def make_worktree(slug: str, *, branch: str) -> Path:
    """A fresh worktree off HEAD. Never checks anything out in the caller's tree."""
    ensure_worktrees_ignored()
    WORKTREES_DIR.mkdir(exist_ok=True)
    path = WORKTREES_DIR / f"optimise-{slug}"
    if path.exists():
        raise RuntimeError(f"{path} already exists — remove it or pick another slug")
    _git(["worktree", "add", "-b", branch, str(path), "HEAD"])
    return path


def commit_and_open_pr(
    *,
    worktree: Path,
    branch: str,
    title: str,
    body: str,
    files: Sequence[str],
    base: str = DEFAULT_BASE_BRANCH,
) -> str:
    """Commit the candidate in its worktree and open a **draft** PR (D4)."""
    _git(["add", *files], cwd=worktree)
    _git(["commit", "-m", body_to_commit_message(title, body)], cwd=worktree)
    _git(["push", "-u", "origin", branch], cwd=worktree)
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {proc.stderr.strip()}")
    return proc.stdout.strip().splitlines()[-1]


def body_to_commit_message(title: str, body: str) -> str:
    return f"{title}\n\n{body.strip()}\n"


# ---------------------------------------------------------------------------
# PR body rendering
# ---------------------------------------------------------------------------


def render_pr_body(
    *,
    kind: str,
    hypothesis: str,
    signals: Sequence[GapSignal],
    files: Sequence[str],
    test_result: dict[str, Any] | None = None,
    case_keys: Sequence[str] = (),
    eval_run_id: str | None = None,
) -> str:
    """The draft PR's body: what was observed, what changed, how it was proved.

    A reviewer's first question is always "what evidence is behind this?", so the
    runs and the verbatim ``skill_gap`` needs come before the change itself.
    """
    lines = [
        f"**Offline optimiser — {kind}** (draft; s49 M3, decision D4: never merged automatically).",
        "",
        "## Hypothesis",
        "",
        hypothesis.strip() or "(none given)",
        "",
        "## Evidence",
        "",
    ]
    if eval_run_id:
        lines += [f"Eval run: `{eval_run_id}`", ""]
    if case_keys:
        lines += ["Cases: " + ", ".join(f"`{k}`" for k in case_keys), ""]
    if signals:
        lines += ["| run | source | need | why |", "| --- | --- | --- | --- |"]
        for s in signals:
            lines.append(
                f"| `{s.run_id[:8]}` | {s.source} | {_cell(s.need)} | {_cell(s.why or '—')} |"
            )
        lines.append("")
    lines += ["## Files", ""]
    lines += [f"- `{f}`" for f in files] or ["- (none)"]
    lines.append("")
    if test_result is not None:
        lines += [
            "## Offline test",
            "",
            "```json",
            json.dumps(test_result, indent=2, sort_keys=True),
            "```",
            "",
        ]
    lines += [
        "---",
        "",
        "🤖 opened by `scripts/"
        + ("skill_miner.py" if kind == "skill" else "reflect.py")
        + "` — review the maths before marking ready.",
    ]
    return "\n".join(lines)


def _cell(text: str) -> str:
    """Markdown-table-safe one-liner."""
    return text.replace("|", "\\|").replace("\n", " ").strip()[:200]


def print_summary(summary: dict[str, Any]) -> None:
    """The machine-readable line M5 greps out of the log (contract item 3)."""
    print(json.dumps(summary, sort_keys=True))
