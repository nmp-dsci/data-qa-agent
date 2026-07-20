"""eval provenance — golden origin_env + eval_run experiment identity (s24 M1)

Two gaps closed so the eval loop is reproducible and comparable:

* ``app.eval_cases.case_key`` — a stable, human-readable identity for a golden
  ('nsw_rent-rent-trends-2077-vs-2076'). The uuid PK is per-database, so it
  cannot survive an export from one environment and an import into another;
  the case_key is what the pack keys on and what ``make eval CASE=…`` selects.
* ``app.eval_cases.origin_env`` — whether a golden was authored in dev or
  promoted from a real prod answer. The repo pack is the source of truth, so
  provenance has to survive the export/import round trip.
* ``app.eval_runs.experiment_id`` / ``base_run_id`` — an improvement attempt is
  a first-class labelled thing, and a run knows which baseline it is arguing
  against. This is what the Evaluations tab renders as "base vs experiment".

Both are additive and nullable: existing rows stay valid, nothing to backfill.

Revision ID: 0029_eval_provenance
Revises: 0028_dataset_ordinals
"""

from __future__ import annotations

from alembic import op

revision = "0029_eval_provenance"
down_revision = "0028_dataset_ordinals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Stable cross-environment identity. Backfilled below from dataset+question
    # so existing goldens get a key without manual curation; unique so an import
    # can upsert on it.
    op.execute("ALTER TABLE app.eval_cases ADD COLUMN IF NOT EXISTS case_key text")

    # Provenance of a golden. 'dev' is the safe default for everything authored
    # before this migration — a prod-promoted case is the exception, not the rule.
    op.execute(
        "ALTER TABLE app.eval_cases ADD COLUMN IF NOT EXISTS origin_env text NOT NULL DEFAULT 'dev'"
    )
    op.execute("ALTER TABLE app.eval_cases DROP CONSTRAINT IF EXISTS eval_cases_origin_env_check")
    op.execute(
        "ALTER TABLE app.eval_cases ADD CONSTRAINT eval_cases_origin_env_check "
        "CHECK (origin_env IN ('dev', 'prod'))"
    )

    # Backfill: '<dataset>-<slugified question>', truncated, with the row's short
    # uuid appended so two similar questions cannot collide. regexp_replace
    # strips punctuation; the uuid suffix guarantees the unique index can be built.
    op.execute(
        """
        UPDATE app.eval_cases SET case_key =
            coalesce(nullif(dataset, ''), 'unknown') || '-' ||
            left(regexp_replace(lower(question), '[^a-z0-9]+', '-', 'g'), 48) || '-' ||
            left(id::text, 4)
        WHERE case_key IS NULL
        """
    )
    # Trim any trailing separator left by truncating mid-word.
    op.execute("UPDATE app.eval_cases SET case_key = regexp_replace(case_key, '-+', '-', 'g')")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS eval_cases_case_key_idx ON app.eval_cases (case_key)"
    )

    # An improvement attempt: a short slug the human chooses ('kb-yield-method'),
    # plus the baseline it is measured against. base_run_id is a self-reference —
    # a baseline run simply leaves it null.
    op.execute("ALTER TABLE app.eval_runs ADD COLUMN IF NOT EXISTS experiment_id text")
    op.execute("ALTER TABLE app.eval_runs ADD COLUMN IF NOT EXISTS hypothesis text")
    op.execute(
        "ALTER TABLE app.eval_runs ADD COLUMN IF NOT EXISTS base_run_id uuid "
        "REFERENCES app.eval_runs(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eval_runs_experiment_idx "
        "ON app.eval_runs (experiment_id, started_at DESC)"
    )

    # The runner (app_user) writes runs and results; admin_ro reads them via the
    # 0012 default privileges. agent_ro never touches eval tables.
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.eval_runs TO app_user")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.eval_results TO app_user")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.agent_versions TO app_user")

    op.execute(
        "INSERT INTO app.schema_migrations (version) VALUES ('0029_eval_provenance') "
        "ON CONFLICT (version) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app.eval_cases_case_key_idx")
    op.execute("ALTER TABLE app.eval_cases DROP COLUMN IF EXISTS case_key")
    op.execute("DROP INDEX IF EXISTS app.eval_runs_experiment_idx")
    op.execute("ALTER TABLE app.eval_runs DROP COLUMN IF EXISTS base_run_id")
    op.execute("ALTER TABLE app.eval_runs DROP COLUMN IF EXISTS hypothesis")
    op.execute("ALTER TABLE app.eval_runs DROP COLUMN IF EXISTS experiment_id")
    op.execute("ALTER TABLE app.eval_cases DROP CONSTRAINT IF EXISTS eval_cases_origin_env_check")
    op.execute("ALTER TABLE app.eval_cases DROP COLUMN IF EXISTS origin_env")
    op.execute("DELETE FROM app.schema_migrations WHERE version = '0029_eval_provenance'")
