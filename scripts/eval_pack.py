#!/usr/bin/env python3
"""Move golden examples between the database and the version-controlled pack (s24 M1).

The repo is the source of truth; the database is a working surface. Admins
author goldens wherever they are — the Golden tab in dev, or "★ save as golden"
on a real answer in prod — and this tool serialises them to
``evals/cases/<dataset>.yaml`` so they can be reviewed in a PR, seeded into a
fresh clone or CI's ephemeral Postgres, and replayed reproducibly.

Without this step a golden authored in prod is invisible to CI, which is exactly
why evals cannot gate anything until it exists.

Usage (from the repo root; the DB is reached via `docker compose exec db`):
    uv run python scripts/eval_pack.py export              # DB  -> evals/cases/*.yaml
    uv run python scripts/eval_pack.py export --dataset nsw_rent
    uv run python scripts/eval_pack.py import              # YAML -> DB (upsert on case_key)
    uv run python scripts/eval_pack.py version             # print the pack content hash

Redaction (decision D-2): a golden promoted from prod carries a real user's
question and real result rows. On export ``as_user`` is mapped to a seeded test
identity and ``golden_data`` is capped, so the pack never becomes a back door
around the RLS model the product exists to demonstrate.

Add `--service NAME` if the db compose service isn't named "db".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "evals" / "cases"

# Identities the pack is allowed to name. A golden replays under RLS as one of
# the seeded users, never as a real prod account.
ALLOWED_USERS = {"user1", "user2", "admin"}
FALLBACK_USER = "user1"

# Cap on stored ground-truth rows. A golden needs enough rows to grade against,
# not the whole result set — and an uncapped dump is how PII reaches git.
MAX_GOLDEN_ROWS = 50

# Per-field ceiling in serialised bytes. Past this a field stops being a
# reviewable specification and becomes a data dump, so it is stored by hash.
MAX_FIELD_BYTES = 16_384

# Columns pulled from app.eval_cases, in pack order.
FIELDS = [
    "case_key",
    "question",
    "dataset",
    "tier",
    "as_user",
    "holdout",
    "origin_env",
    "authoring_status",
    "expectation",
    "tags",
    "grader",
    "golden_sql",
    "golden_sandbox",
    "golden_objects",
    "golden_data",
    "golden_report",
]

# ``golden_data`` is *derived*, not specified: G1 grades the agent's extracted
# values against what ``golden_sql`` returns when the eval runs, so the rows do
# not need to live in git. The pack therefore carries only a digest of them —
# enough to detect golden rot when a mart changes underneath a case — and import
# never writes the column, so a round trip cannot clobber the real rows with a
# summary of themselves.
DERIVED_FIELDS = {"golden_data"}


def _psql(query: str, service: str) -> str:
    """Run one statement in the compose Postgres and return raw stdout."""
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


def _digest(value: Any) -> str:
    """Short content hash of any JSON-able value."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]


def _cap_rows(node: Any) -> Any:
    """Recursively truncate every embedded data array to a shorter *array*.

    Bulk data hides at many depths and under many names — ``queries[].rows``, a
    chart spec's ``values``, per-insight payloads. Dumping it all made a single
    golden a 24k-line file: unreviewable in a PR, which defeats the reason the
    pack is version-controlled, and the most likely way real result data reaches
    git. So the rule is by shape, not key name: any list longer than
    ``MAX_GOLDEN_ROWS`` is truncated to its head.

    Critically, a truncated list stays a *list*. ``_cap_rows`` is only ever
    applied to ``golden_report``/``golden_objects`` (see ``_redact``) — the
    fields the frontend *renders* — and a chart whose ``rows`` became a
    ``{_truncated, _head, …}`` dict is unrenderable: the renderer iterates it and
    throws, blanking the page. Drift in the ground-truth values is tracked by
    ``golden_data_sha`` (``golden_data`` is digested separately), not by these
    presentation rows, so the head is all the pack needs.
    """
    if isinstance(node, list):
        return [_cap_rows(item) for item in node[:MAX_GOLDEN_ROWS]]
    if isinstance(node, dict):
        return {key: _cap_rows(value) for key, value in node.items()}
    return node


def _budget(field: str, value: Any) -> Any:
    """Replace a field that is still oversized with a digest stub.

    A backstop for payloads that are large without being list-shaped (a long
    generated narrative, a chart spec with inlined data). The pack is a
    specification; anything past the budget is a data dump that a reviewer
    cannot meaningfully read, so it is recorded by hash instead of by value.
    """
    encoded = json.dumps(value, default=str)
    if len(encoded) <= MAX_FIELD_BYTES:
        return value
    return {"_omitted": True, "_bytes": len(encoded), "_sha": _digest(value), "_field": field}


def _redact(case: dict[str, Any]) -> dict[str, Any]:
    """Strip anything that must not enter version control."""
    if case.get("as_user") not in ALLOWED_USERS:
        case["as_user"] = FALLBACK_USER
    # Derived rows leave as a digest only — see DERIVED_FIELDS.
    data = case.pop("golden_data", None)
    if data is not None:
        case["golden_data_sha"] = _digest(data)
    for field in ("golden_report", "golden_objects"):
        if case.get(field) is not None:
            case[field] = _cap_rows(case[field])
    return case


def _fetch(service: str, dataset: str | None) -> list[dict[str, Any]]:
    """Every non-archived golden, newest last, as parsed dicts."""
    where = "status <> 'archived'"
    if dataset:
        where += f" AND dataset = {_sql_literal(dataset)}"
    cols = ", ".join(f"'{f}', {f}" for f in FIELDS)
    raw = _psql(
        f"SELECT coalesce(json_agg(json_build_object({cols}) ORDER BY created_at), '[]')"
        f" FROM app.eval_cases WHERE {where}",
        service,
    )
    return [_redact(c) for c in json.loads(raw or "[]")]


def _dump(cases: list[dict[str, Any]], dataset: str) -> str:
    """Serialise one dataset's cases to the pack's YAML shape."""
    keys = [f for f in FIELDS if f not in DERIVED_FIELDS] + ["golden_data_sha"]
    doc = {
        "pack": "nsw_property",
        "dataset": dataset,
        "cases": [{k: c.get(k) for k in keys if c.get(k) not in (None, "", [])} for c in cases],
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def pack_version() -> str:
    """Content hash of the whole pack — recorded on every eval_run.

    Hashes the serialised bytes of every case file in sorted order, so any edit
    to any golden produces a new pack_version and a score can never be silently
    compared against a different specification.
    """
    if not CASES_DIR.is_dir():
        return "none"
    h = hashlib.sha256()
    for path in sorted(CASES_DIR.glob("*.yaml")):
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return f"pv-{h.hexdigest()[:8]}"


def cmd_export(args: argparse.Namespace) -> None:
    cases = _fetch(args.service, args.dataset)
    if not cases:
        sys.exit("no goldens matched — nothing exported")
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    by_dataset: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        by_dataset.setdefault(case.get("dataset") or "unknown", []).append(case)
    for dataset, group in sorted(by_dataset.items()):
        path = CASES_DIR / f"{dataset}.yaml"
        path.write_text(_dump(group, dataset), encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)} ({len(group)} case(s))")
    print(f"pack_version {pack_version()}")


def _sql_literal(value: Any) -> str:
    """Quote a value for inlining, JSON-encoding structured fields."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value).replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def cmd_import(args: argparse.Namespace) -> None:
    """Upsert every pack case into the DB, keyed on case_key.

    Idempotent: re-importing an unchanged pack is a no-op. This is how a fresh
    clone, CI, or prod gets the same goldens the pack author reviewed.
    """
    if not CASES_DIR.is_dir():
        sys.exit(f"no pack at {CASES_DIR.relative_to(REPO_ROOT)} — run export first")
    statements: list[str] = []
    count = 0
    for path in sorted(CASES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in doc.get("cases") or []:
            if not case.get("case_key"):
                sys.exit(f"{path.name}: a case is missing case_key")
            # jsonb columns need an explicit cast; text/bool ones must not have one.
            jsonb = {"tags", "grader", "golden_objects", "golden_data", "golden_report"}
            # Never write derived columns, and never write a field the export
            # reduced to a digest stub — in both cases the database holds the
            # real value and the pack holds only a summary of it.
            cols = [
                f
                for f in FIELDS
                if f in case
                and f not in DERIVED_FIELDS
                and not (isinstance(case[f], dict) and case[f].get("_omitted"))
            ]
            vals = [_sql_literal(case[f]) + ("::jsonb" if f in jsonb else "") for f in cols]
            updates = ", ".join(f"{f} = EXCLUDED.{f}" for f in cols if f != "case_key")
            statements.append(
                f"INSERT INTO app.eval_cases ({', '.join(cols)}, source) "
                f"VALUES ({', '.join(vals)}, 'authored') "
                f"ON CONFLICT (case_key) DO UPDATE SET {updates}, updated_at = now();"
            )
            count += 1
    if not statements:
        sys.exit("pack is empty — nothing imported")
    _psql("BEGIN; " + " ".join(statements) + " COMMIT;", args.service)
    print(f"imported {count} case(s) at pack_version {pack_version()}")


def cmd_version(_args: argparse.Namespace) -> None:
    print(pack_version())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--service", default="db", help="compose service running Postgres")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="DB -> evals/cases/*.yaml")
    p_export.add_argument("--dataset", help="only this dataset slug")
    p_export.set_defaults(func=cmd_export)

    sub.add_parser("import", help="evals/cases/*.yaml -> DB").set_defaults(func=cmd_import)
    sub.add_parser("version", help="print the pack content hash").set_defaults(func=cmd_version)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
