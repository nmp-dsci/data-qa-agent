"""The free PR gate over the golden pack (s24 M3).

Deterministic and zero-LLM-cost, so it can block every merge. It does not score
the agent — it checks that the *specification* is still valid, because a golden
that cannot regenerate its own answer will grade the agent against a broken
benchmark and nobody will notice.

What it catches:

* golden rot — a mart changes and a golden's SQL stops running
* pack breakage — malformed YAML, duplicate or missing case keys
* leakage — a real user id or an oversized data dump reaching git
* grader drift — a grader spec the runner cannot dispatch

The SQL checks need a live database and self-skip without one, exactly like the
journey evals, so the suite stays green on a laptop with the stack down.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "evals" / "cases"

# Mirrors scripts/eval_pack.py — the pack may only name seeded test identities.
ALLOWED_USERS = {"user1", "user2", "admin"}
MAX_CASE_BYTES = 64_000
VALID_KINDS = {"scalar", "row_set", "ranked_set", "series"}
VALID_AGGREGATES = {"sum", "ratio"}
VALID_TIERS = {f"T{n}" for n in range(1, 8)}


def _load_cases() -> list[tuple[str, dict[str, Any]]]:
    if not CASES_DIR.is_dir():
        return []
    out = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in doc.get("cases") or []:
            out.append((path.name, case))
    return out


CASES = _load_cases()


def _db_available() -> bool:
    try:
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
                "select 1",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 - no docker, no db: skip rather than fail
        return False


DB_UP = _db_available()


def test_pack_is_not_empty() -> None:
    """A pack that silently became empty would make every eval trivially pass."""
    assert CASES, "no goldens in evals/cases — run `make eval-export`"


def test_case_keys_are_unique() -> None:
    """case_key is what import upserts on and what `make eval CASE=` selects."""
    keys = [c.get("case_key") for _, c in CASES]
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, f"duplicate case_key(s): {sorted(duplicates)}"


@pytest.mark.parametrize("filename,case", CASES, ids=[c.get("case_key", "?") for _, c in CASES])
def test_case_is_well_formed(filename: str, case: dict[str, Any]) -> None:
    key = case.get("case_key")
    assert key, f"{filename}: case with no case_key"
    assert case.get("question"), f"{key}: no question"
    assert case.get("dataset"), f"{key}: no dataset"
    tier = case.get("tier")
    assert tier in VALID_TIERS, f"{key}: tier {tier!r} not in {sorted(VALID_TIERS)}"
    assert case.get("origin_env") in {"dev", "prod", None}, f"{key}: bad origin_env"


@pytest.mark.parametrize("filename,case", CASES, ids=[c.get("case_key", "?") for _, c in CASES])
def test_case_leaks_nothing(filename: str, case: dict[str, Any]) -> None:
    """Redaction is a gate, not a convention.

    A golden promoted from prod carries a real question and real rows. For a
    product whose whole thesis is RLS governance, letting that reach git is the
    sharpest own-goal available — so the pack may only name seeded identities,
    and a case that grew into a data dump fails here.
    """
    key = case.get("case_key")
    as_user = case.get("as_user")
    assert as_user in ALLOWED_USERS, (
        f"{key}: as_user {as_user!r} is not a seeded test identity {sorted(ALLOWED_USERS)}"
    )
    size = len(json.dumps(case, default=str))
    assert size <= MAX_CASE_BYTES, (
        f"{key}: {size} bytes — past the budget, so it is a data dump rather than "
        "a reviewable specification. Re-export to re-apply capping."
    )


@pytest.mark.parametrize("filename,case", CASES, ids=[c.get("case_key", "?") for _, c in CASES])
def test_grader_spec_is_dispatchable(filename: str, case: dict[str, Any]) -> None:
    """A grader the runner cannot dispatch scores 0 and looks like an agent failure."""
    key = case.get("case_key")
    spec = case.get("grader") or {}
    if not spec:
        pytest.skip(f"{key}: no grader spec — G1 will report 'no kind' rather than score")
    kind = spec.get("kind")
    assert kind in VALID_KINDS, f"{key}: grader.kind {kind!r} not in {sorted(VALID_KINDS)}"
    if kind in {"row_set", "ranked_set", "series"}:
        assert spec.get("key"), f"{key}: {kind} grader needs a key"
    if kind == "series":
        assert spec.get("value"), f"{key}: series grader needs a value column"
    if spec.get("key") == "_key":
        assert spec.get("key_fields"), f"{key}: key '_key' requires key_fields to build it"
    if spec.get("aggregate"):
        assert spec["aggregate"] in VALID_AGGREGATES, (
            f"{key}: unknown aggregate {spec['aggregate']!r}"
        )


@pytest.mark.skipif(not DB_UP, reason="database not running — SQL checks need the stack up")
@pytest.mark.parametrize("filename,case", CASES, ids=[c.get("case_key", "?") for _, c in CASES])
def test_golden_sql_still_runs(filename: str, case: dict[str, Any]) -> None:
    """EXPLAIN every golden against the live schema — the golden-rot check.

    EXPLAIN rather than execute: it validates tables, columns and types against
    the migrated schema for free, without moving rows.
    """
    key = case.get("case_key")
    sql = case.get("golden_sql")
    if not sql:
        pytest.skip(f"{key}: no golden_sql")
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
            f"EXPLAIN {sql}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"{key}: golden_sql no longer runs against the current schema —\n{proc.stderr.strip()}"
    )


@pytest.mark.skipif(not DB_UP, reason="database not running")
@pytest.mark.parametrize("filename,case", CASES, ids=[c.get("case_key", "?") for _, c in CASES])
def test_grader_columns_exist_in_golden_sql(filename: str, case: dict[str, Any]) -> None:
    """The columns a grader keys on must actually be produced by the golden SQL.

    Otherwise G1 silently compares nothing and reports a confident zero — the
    failure mode that made this pack's first scored run look like an agent bug
    when it was a grain mismatch in the spec.
    """
    key = case.get("case_key")
    spec = case.get("grader") or {}
    sql = case.get("golden_sql")
    if not spec or not sql:
        pytest.skip(f"{key}: nothing to check")
    wanted = [c for c in list(spec.get("key_fields") or []) if c]
    # A ratio grader reconstructs the rate from numerator/denominator, so the
    # golden SQL is only required to produce *those*; the named value column is
    # what the agent is expected to return, and need not exist on the golden side.
    if spec.get("aggregate") == "ratio":
        wanted += [c for c in (spec.get("numerator"), spec.get("denominator")) if c]
    elif spec.get("value"):
        wanted.append(str(spec["value"]))
    if not wanted:
        pytest.skip(f"{key}: grader names no columns")
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
            f"SELECT * FROM ({sql}) _probe LIMIT 0",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        pytest.skip(f"{key}: golden_sql not probeable here")
    # -tA with LIMIT 0 prints nothing, so ask the server for the column names.
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
            "-c",
            f"SELECT * FROM ({sql}) _probe LIMIT 0",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    header = proc.stdout.splitlines()[0] if proc.stdout.strip() else ""
    produced = {c.strip() for c in header.split("|")}
    missing = [c for c in wanted if c not in produced]
    assert not missing, (
        f"{key}: grader names column(s) {missing} that golden_sql does not produce "
        f"(produces: {sorted(produced)})"
    )


def test_pack_matches_committed_version() -> None:
    """The recorded pack_version must match the files on disk.

    Catches a pack edited by hand without re-exporting, which would make every
    stored score reference a specification that no longer exists.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from eval_pack import pack_version  # noqa: PLC0415

    version = pack_version()
    assert version.startswith("pv-"), f"unexpected pack_version {version!r}"
    assert version != "pv-" and len(version) > 4, "pack_version did not hash any files"
    if os.environ.get("EXPECTED_PACK_VERSION"):
        assert version == os.environ["EXPECTED_PACK_VERSION"]
