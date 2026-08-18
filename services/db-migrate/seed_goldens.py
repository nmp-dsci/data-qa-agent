"""Import the version-controlled goldens pack into app.eval_cases (s38 P1).

Closes the dev→prod content gap: until now curated goldens lived only in
whichever database they were authored against — the repo pack existed
(``evals/cases/*.yaml``, written by ``scripts/eval_pack.py export``) but no
deploy step ever imported it, so prod's goldens gallery could sit empty while
dev's was rich. This runs inside the migrate job, which executes on every
deploy AND on every local ``make up``, so every environment converges on the
reviewed pack.

Same upsert semantics as ``eval_pack.py import`` (keyed on case_key,
idempotent), reimplemented over psycopg because this job talks straight to the
database — no docker compose exec here. Skips silently when the pack directory
isn't in the image (older images) and never fails the migration run: goldens
are content, not schema.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
import yaml

PACK_DIR = Path(__file__).resolve().parent / "evals" / "cases"

# Mirror of eval_pack.py: importable columns, jsonb casts, derived fields the
# pack only ever carries as digests.
FIELDS = [
    "case_key", "question", "dataset", "tier", "as_user", "holdout", "origin_env",
    "authoring_status", "expectation", "tags", "grader", "golden_sql",
    "golden_sandbox", "golden_objects", "golden_data", "golden_report",
]
JSONB = {"tags", "grader", "golden_objects", "golden_data", "golden_report"}
DERIVED = {"golden_data"}


def _param(case: dict[str, Any], field: str) -> Any:
    value = case[field]
    if field in JSONB:
        return json.dumps(value)
    return value


def main() -> None:
    url = os.environ.get("ADMIN_DATABASE_URL")
    if not url:
        print("seed_goldens: ADMIN_DATABASE_URL not set — skipping")
        return
    if not PACK_DIR.is_dir():
        print("seed_goldens: no pack directory in image — skipping")
        return
    cases: list[dict[str, Any]] = []
    for path in sorted(PACK_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cases.extend(doc.get("cases") or [])
    if not cases:
        print("seed_goldens: pack is empty — nothing to import")
        return

    imported = 0
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        for case in cases:
            if not case.get("case_key"):
                print(f"seed_goldens: skipping a case with no case_key")
                continue
            cols = [
                f for f in FIELDS
                if f in case
                and f not in DERIVED
                and not (isinstance(case[f], dict) and case[f].get("_omitted"))
            ]
            placeholders = ", ".join(
                f"%({f})s::jsonb" if f in JSONB else f"%({f})s" for f in cols
            )
            updates = ", ".join(f"{f} = EXCLUDED.{f}" for f in cols if f != "case_key")
            cur.execute(
                f"INSERT INTO app.eval_cases ({', '.join(cols)}, source) "
                f"VALUES ({placeholders}, 'authored') "
                f"ON CONFLICT (case_key) DO UPDATE SET {updates}, updated_at = now()",
                {f: _param(case, f) for f in cols},
            )
            imported += 1
    print(f"seed_goldens: imported {imported} case(s)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — content, not schema: warn, don't fail deploys
        print(f"seed_goldens: import failed (non-fatal): {exc}", file=sys.stderr)
