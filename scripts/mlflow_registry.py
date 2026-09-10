"""Agent registry + promotion CLI over MLflow (s43 M0/M2/M3, bundle s49 M1).

Subcommands:
  init      ensure the data-qa/traces + data-qa/evals experiments exist; print
            ids and warn when the traces id differs from what the services'
            MLFLOW_TRACE_EXPERIMENT_ID assumes (default 1 on a fresh store).
  ensure    mirror app.agent_versions -> model versions of `data-qa-agent`,
            one per fingerprint, params = the composed build fingerprint. Also
            logs a `bundle.json` + `bundle.tar.gz` artifact pair to the
            register run (s49 M1) — a portable snapshot of the prompts/skills/
            knowledge that produced this fingerprint, so `agent_checkout.py`
            can reproduce it even without the git sha (e.g. uncommitted local
            work). Bootstrap aliases: @champion = the build the live agent
            reports (fallback: newest), @challenger = the newest other build,
            if any.
  status    print versions, aliases, and each version's latest eval pass-rate.
  promote   the comparator gate (ConvFinQA rule): the challenger's latest eval
            on the same pack must have pass_rate >= champion's AND no golden
            that passed for the champion may fail for the challenger. On PASS
            the @champion alias moves, @challenger is cleared, and an
            append-only row lands in app.promotions.

Postgres stays the operational source of truth (adjustment 2 in the s43 plan):
MLflow is the comparison UI and the alias mechanics; `ensure` is idempotent and
re-runnable after any new build shows up in app.agent_versions.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mlflow_client as mc  # noqa: E402

AGENT = "http://localhost:8100"
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_AGENT_DIR = REPO_ROOT / "services" / "data-agent"

# The behaviour-surface directories a `bundle.tar.gz` snapshots (s49 M1) — the
# same surfaces build_sdk_fingerprint()/build_fingerprint() hash, minus the
# schema/marts (those come from the DB catalogue, not files a checkout could
# usefully restore). agent_checkout.py extracts this tree relative to
# services/data-agent/ so the paths below double as the tar's arcnames.
BUNDLE_DIRS = (
    Path("agent") / "prompts",
    Path("agent") / "skills",
    Path("knowledge"),
)

FINGERPRINT_COLS = (
    "id, fingerprint, label, provider, model_id, prompt_hash, skills_hash, "
    "knowledge_version, image_tag, git_sha, created_at"
)


def _db():
    """The eval runner's psql helpers, imported lazily so `init` needs no DB."""
    from eval_run import _lit, _psql, _scalar  # noqa: PLC0415

    return _lit, _psql, _scalar


def _agent_versions() -> list[dict[str, Any]]:
    _lit, _psql, _ = _db()
    out = _psql(
        f"SELECT row_to_json(t) FROM (SELECT {FINGERPRINT_COLS} "
        "FROM app.agent_versions ORDER BY created_at) t"
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _live_fingerprint() -> str | None:
    try:
        with urllib.request.urlopen(f"{AGENT}/agent/version", timeout=5) as resp:
            return json.load(resp).get("fingerprint")
    except Exception:  # noqa: BLE001 — agent down is fine; fallback applies
        return None


# ---- bundle (s49 M1) --------------------------------------------------------
#
# `agent_checkout.py FP` needs to reproduce the prompts/skills/knowledge that
# produced a fingerprint. `git_sha` is the preferred route (a worktree at that
# commit), but a fingerprint minted from uncommitted local work has no
# reachable commit — the tarball is the fallback that always works, captured
# from the working tree at the moment `ensure` registers the fingerprint.


def _psql_soft(query: str) -> str | None:
    """Like eval_run.py's `_psql`, but returns None on failure instead of
    exiting the whole `ensure` run — the ordinals/knowledge_pages tables this
    is used for are optional (a fresh dev DB, or migration 0039 not applied
    yet), and a missing one must degrade the bundle, not the registry sync."""
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
        return None
    return proc.stdout.strip()


# Mirrors agent.ordinals.BAND_ORDERS — duplicated rather than imported so this
# script stays independent of the data-agent package (same grain as
# mlflow_client.py's stdlib-only REST client).
_ORDINALS_SEED: dict[tuple[str, str], list[str]] = {
    ("nsw_sales", "area_band"): ["<400", "400-700", "700-1000", "1000-5000", "5000+", "unknown"],
    ("nsw_rent", "bedroom_band"): ["0", "1", "2", "3", "4", "5+", "unknown"],
}


def _ordinals_db_hash() -> str:
    """Same canonicalisation as agent.ordinals.ordinals_snapshot_hash(): the
    code seed merged with any app.dataset_ordinals curator overrides, hashed
    as sorted tight-separator JSON. A DB miss (table absent, service down)
    degrades to hashing the seed alone, never raises."""
    merged = dict(_ORDINALS_SEED)
    out = _psql_soft(
        "SELECT row_to_json(t) FROM (SELECT d.slug, o.column_name, o.ordered_values "
        "FROM app.dataset_ordinals o JOIN app.datasets d ON d.id = o.dataset_id) t"
    )
    for line in (out or "").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        vals = row.get("ordered_values")
        if isinstance(vals, list) and vals:
            merged[(str(row["slug"]), str(row["column_name"]))] = [str(v) for v in vals]
    canonical = [{"dataset": d, "column": c, "order": o} for (d, c), o in sorted(merged.items())]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _knowledge_pages_db_hash() -> str | None:
    """Content hash of app.knowledge_pages (curator overrides), or None when
    the table has no rows or doesn't exist yet (migration 0039)."""
    out = _psql_soft(
        "SELECT row_to_json(t) FROM (SELECT path, version, body FROM app.knowledge_pages "
        "ORDER BY path) t"
    )
    if not out:
        return None
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return None
    h = hashlib.sha256()
    for row in rows:
        h.update(str(row.get("path", "")).encode("utf-8"))
        h.update(b"\0")
        h.update(str(row.get("version", "")).encode("utf-8"))
        h.update(b"\0")
        h.update(str(row.get("body") or "").encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _bundle_files() -> list[Path]:
    """Every file under the bundled directories, relative to services/data-agent/."""
    files: list[Path] = []
    for rel_dir in BUNDLE_DIRS:
        abs_dir = DATA_AGENT_DIR / rel_dir
        if not abs_dir.is_dir():
            continue
        for path in sorted(abs_dir.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts:
                continue
            files.append(path.relative_to(DATA_AGENT_DIR))
    return files


def _bundle_tarball(files: list[Path]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in files:
            tar.add(DATA_AGENT_DIR / rel, arcname=rel.as_posix())
    return buf.getvalue()


def _git_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _log_bundle_artifacts(run_id: str, row: dict[str, Any], fp: str) -> None:
    """Log bundle.json + bundle.tar.gz to a register run and tag the model
    version so agent_checkout.py can find them from the fingerprint alone."""
    files = _bundle_files()
    components = {
        k: row.get(k)
        for k in ("provider", "model_id", "prompt_hash", "skills_hash", "knowledge_version")
        if row.get(k)
    }
    bundle = {
        "fingerprint": fp,
        "components": components,
        "git_sha": row.get("git_sha") or _git_sha(),
        "image_tag": row.get("image_tag") or "",
        "files": [f.as_posix() for f in files],
        "db_snapshots": {
            "ordinals": _ordinals_db_hash(),
            "knowledge_pages": _knowledge_pages_db_hash(),
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    mc.log_json_artifact(run_id, "bundle.json", bundle)
    mc.log_artifact(
        run_id, "bundle.tar.gz", _bundle_tarball(files), content_type="application/gzip"
    )


# ---- init ------------------------------------------------------------------


def cmd_init(_: argparse.Namespace) -> None:
    traces_id = mc.ensure_experiment(mc.TRACES_EXPERIMENT)
    evals_id = mc.ensure_experiment(mc.EVALS_EXPERIMENT)
    print(f"experiment {mc.TRACES_EXPERIMENT!r}: id {traces_id}")
    print(f"experiment {mc.EVALS_EXPERIMENT!r}: id {evals_id}")
    import os

    assumed = os.environ.get("MLFLOW_TRACE_EXPERIMENT_ID", "1")
    if traces_id != assumed:
        print(
            f"WARNING: services default MLFLOW_TRACE_EXPERIMENT_ID={assumed} but the traces "
            f"experiment id is {traces_id} — set MLFLOW_TRACE_EXPERIMENT_ID={traces_id} in .env "
            "and recreate backend-api/data-agent, or spans will land in the wrong experiment."
        )


# ---- ensure ----------------------------------------------------------------


def cmd_ensure(_: argparse.Namespace) -> None:
    mc.ensure_registered_model(mc.MODEL_NAME)
    evals_id = mc.ensure_experiment(mc.EVALS_EXPERIMENT)

    by_fingerprint: dict[str, str] = {}
    for v in mc.search_model_versions(mc.MODEL_NAME):
        tags = {t["key"]: t["value"] for t in v.get("tags", [])}
        if "fingerprint" in tags:
            by_fingerprint[tags["fingerprint"]] = v["version"]

    created = 0
    for row in _agent_versions():
        fp = row["fingerprint"]
        if fp in by_fingerprint:
            continue
        # A "register" run carries the composed fingerprint as params, so the
        # registry version has a comparable, filterable record behind it.
        run_id = mc.start_run(
            evals_id,
            f"register {fp}",
            tags={"kind": "register", "agent_version_id": row["id"], "fingerprint": fp},
        )
        mc.log_batch(
            run_id,
            params={
                "provider": row["provider"],
                "model_id": row["model_id"],
                "prompt_hash": row["prompt_hash"],
                "skills_hash": row["skills_hash"],
                "knowledge_version": row["knowledge_version"],
                "image_tag": row["image_tag"],
                "git_sha": row["git_sha"],
            },
        )
        _log_bundle_artifacts(run_id, row, fp)
        mc.end_run(run_id)
        version = mc.create_model_version(
            mc.MODEL_NAME,
            source=f"runs:/{run_id}/agent",
            run_id=run_id,
            tags={
                "fingerprint": fp,
                "agent_version_id": row["id"],
                "provider": row["provider"],
                "model_id": row["model_id"],
                "bundle_run_id": run_id,
            },
            description=row.get("label") or fp,
        )
        by_fingerprint[fp] = version
        created += 1
        print(f"registered {fp} -> {mc.MODEL_NAME} v{version}")
    if not created:
        print("no new agent versions to register")

    # Bootstrap aliases. Champion = the build actually serving (the live agent's
    # fingerprint); a promotion moves it afterwards, never this sync.
    if mc.get_alias_version(mc.MODEL_NAME, mc.CHAMPION) is None and by_fingerprint:
        live = _live_fingerprint()
        rows = _agent_versions()
        newest = rows[-1]["fingerprint"] if rows else None
        champ_fp = live if live in by_fingerprint else newest
        if champ_fp in by_fingerprint:
            mc.set_alias(mc.MODEL_NAME, mc.CHAMPION, by_fingerprint[champ_fp])
            print(f"@champion -> v{by_fingerprint[champ_fp]} ({champ_fp})")
    champ_v = mc.get_alias_version(mc.MODEL_NAME, mc.CHAMPION)
    if champ_v is not None and mc.get_alias_version(mc.MODEL_NAME, mc.CHALLENGER) is None:
        others = [v for v in by_fingerprint.values() if v != champ_v]
        if others:
            newest_other = max(others, key=int)
            mc.set_alias(mc.MODEL_NAME, mc.CHALLENGER, newest_other)
            print(f"@challenger -> v{newest_other}")


# ---- status / promote ------------------------------------------------------


def _latest_eval(agent_version_id: str) -> dict[str, Any] | None:
    _lit, _psql, _scalar = _db()
    out = _scalar(
        "SELECT row_to_json(t) FROM (SELECT id, pack_version, totals, started_at "
        f"FROM app.eval_runs WHERE agent_version_id = {_lit(agent_version_id)}::uuid "
        "ORDER BY started_at DESC LIMIT 1) t"
    )
    return json.loads(out) if out else None


def _passed_cases(eval_run_id: str) -> set[str]:
    _lit, _psql, _ = _db()
    out = _psql(
        "SELECT c.case_key FROM app.eval_results r JOIN app.eval_cases c ON c.id = r.case_id "
        f"WHERE r.eval_run_id = {_lit(eval_run_id)}::uuid AND r.passed"
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def _version_info(version: str) -> dict[str, str]:
    for v in mc.search_model_versions(mc.MODEL_NAME):
        if v["version"] == version:
            return {t["key"]: t["value"] for t in v.get("tags", [])}
    return {}


def cmd_status(_: argparse.Namespace) -> None:
    champ = mc.get_alias_version(mc.MODEL_NAME, mc.CHAMPION)
    chall = mc.get_alias_version(mc.MODEL_NAME, mc.CHALLENGER)
    for v in sorted(mc.search_model_versions(mc.MODEL_NAME), key=lambda v: int(v["version"])):
        tags = {t["key"]: t["value"] for t in v.get("tags", [])}
        marks = []
        if v["version"] == champ:
            marks.append("@champion")
        if v["version"] == chall:
            marks.append("@challenger")
        avid = tags.get("agent_version_id")
        ev = _latest_eval(avid) if avid else None
        rate = (ev or {}).get("totals", {}).get("pass_rate")
        print(
            f"v{v['version']}  {tags.get('fingerprint', '?'):20s} "
            f"{' '.join(marks):22s} latest eval pass_rate={rate}"
        )


def cmd_promote(args: argparse.Namespace) -> None:
    _lit, _psql, _scalar = _db()
    champ_v = mc.get_alias_version(mc.MODEL_NAME, mc.CHAMPION)
    chall_v = mc.get_alias_version(mc.MODEL_NAME, mc.CHALLENGER)
    if champ_v is None or chall_v is None:
        sys.exit("promote: need both @champion and @challenger aliases (run `ensure` first)")
    champ_tags, chall_tags = _version_info(champ_v), _version_info(chall_v)
    champ_eval = _latest_eval(champ_tags.get("agent_version_id", ""))
    chall_eval = _latest_eval(chall_tags.get("agent_version_id", ""))
    if not champ_eval or not chall_eval:
        sys.exit("promote: both champion and challenger need at least one eval run (make eval)")
    if champ_eval["pack_version"] != chall_eval["pack_version"]:
        sys.exit(
            f"promote: eval packs differ (champion {champ_eval['pack_version']} vs "
            f"challenger {chall_eval['pack_version']}) — not a comparable measurement"
        )

    champ_rate = champ_eval["totals"].get("pass_rate") or 0.0
    chall_rate = chall_eval["totals"].get("pass_rate") or 0.0
    flips = sorted(_passed_cases(champ_eval["id"]) - _passed_cases(chall_eval["id"]))
    verdict = {
        "rule": "pass_rate >= champion AND no pass->fail flips",
        "champion": {"version": champ_v, "eval_run": champ_eval["id"], "pass_rate": champ_rate},
        "challenger": {"version": chall_v, "eval_run": chall_eval["id"], "pass_rate": chall_rate},
        "pack_version": champ_eval["pack_version"],
        "flips": flips,
        "promoted": chall_rate >= champ_rate and not flips,
    }
    print(json.dumps(verdict, indent=2))
    if not verdict["promoted"]:
        print("\nHOLD — challenger does not clear the gate; aliases unchanged.")
        return

    mc.set_alias(mc.MODEL_NAME, mc.CHAMPION, chall_v)
    mc.delete_alias(mc.MODEL_NAME, mc.CHALLENGER)
    _psql(
        "INSERT INTO app.promotions (model_name, from_version, to_version, agent_version_id, "
        f"verdict) VALUES ({_lit(mc.MODEL_NAME)}, {_lit(champ_v)}, {_lit(chall_v)}, "
        f"{_lit(chall_tags.get('agent_version_id'))}::uuid, {_lit(verdict)}::jsonb)"
    )
    print(f"\nPROMOTED — @champion moved v{champ_v} -> v{chall_v}; recorded in app.promotions.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("ensure").set_defaults(fn=cmd_ensure)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("promote").set_defaults(fn=cmd_promote)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
